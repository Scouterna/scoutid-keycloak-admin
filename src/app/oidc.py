"""Logging users in against Keycloak.

Deliberately no OIDC client library, following wsj27-auth-api: the code grant
is one form POST, and rolling it by hand lets redirect_uri come from PUBLIC_URL
in both the authorization and the token request instead of being rebuilt from
X-Forwarded-* headers.

The browser is sent to the public Keycloak host; the backend talks to the
internal one. Keycloak's hostname is fixed in the chart, so tokens fetched over
the internal URL still carry the public issuer.
"""

import base64
import hashlib
import logging
import secrets
import time
from typing import Any
from urllib.parse import urlencode

import httpx
from joserfc import jwt
from joserfc.errors import JoseError
from joserfc.jwk import KeySet

from .config import Settings

logger = logging.getLogger(__name__)

SCOPE = "openid profile scoutnet-memberships"
ALGORITHMS = ["RS256"]
# At most one JWKS refetch per this many seconds, however many tokens with an
# unknown kid turn up.
JWKS_REFETCH_INTERVAL_SECONDS = 60


class OIDCError(Exception):
    """A login could not be completed."""


def generate_pkce_pair() -> tuple[str, str]:
    """Return (code_verifier, code_challenge) for the S256 method."""
    verifier = secrets.token_urlsafe(64)
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    challenge = base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")
    return verifier, challenge


class OidcClient:
    def __init__(self, settings: Settings, http: httpx.AsyncClient) -> None:
        self._settings = settings
        self._http = http
        self._jwks: KeySet | None = None
        self._jwks_fetched_at = 0.0

    @property
    def callback_url(self) -> str:
        return f"{self._settings.PUBLIC_URL}/auth/callback"

    def authorization_url(self, *, state: str, code_challenge: str, nonce: str) -> str:
        params = {
            "client_id": self._settings.KC_CLIENT_ID,
            "response_type": "code",
            "redirect_uri": self.callback_url,
            "scope": SCOPE,
            "state": state,
            "nonce": nonce,
            "code_challenge": code_challenge,
            "code_challenge_method": "S256",
        }
        return f"{self._settings.issuer}/protocol/openid-connect/auth?{urlencode(params)}"

    def end_session_url(self, id_token: str | None) -> str:
        params = {
            "client_id": self._settings.KC_CLIENT_ID,
            "post_logout_redirect_uri": f"{self._settings.PUBLIC_URL}/",
        }
        if id_token:
            # Without the hint Keycloak asks the user to confirm the logout
            # before returning them.
            params["id_token_hint"] = id_token
        return f"{self._settings.issuer}/protocol/openid-connect/logout?{urlencode(params)}"

    async def exchange_code(self, *, code: str, code_verifier: str) -> dict[str, Any]:
        try:
            response = await self._http.post(
                f"{self._settings.internal_realm_url}/protocol/openid-connect/token",
                data={
                    "grant_type": "authorization_code",
                    "code": code,
                    "redirect_uri": self.callback_url,
                    "code_verifier": code_verifier,
                },
                # client_secret_basic, Keycloak's default for a confidential client.
                auth=(self._settings.KC_CLIENT_ID, self._settings.KC_CLIENT_SECRET),
                headers={"Accept": "application/json"},
            )
        except httpx.HTTPError as e:
            raise OIDCError(f"Token endpoint unreachable: {e!r}") from e
        if response.status_code >= 400:
            raise OIDCError(f"Token endpoint returned {response.status_code}: {response.text}")
        return response.json()

    # --- id_token verification ---

    async def _keyset(self, *, refresh: bool = False) -> KeySet:
        stale = time.monotonic() - self._jwks_fetched_at > JWKS_REFETCH_INTERVAL_SECONDS
        if self._jwks is None or (refresh and stale):
            try:
                response = await self._http.get(f"{self._settings.internal_realm_url}/protocol/openid-connect/certs")
                response.raise_for_status()
            except httpx.HTTPError as e:
                raise OIDCError(f"JWKS unavailable: {e!r}") from e
            self._jwks = KeySet.import_key_set(response.json())
            self._jwks_fetched_at = time.monotonic()
        return self._jwks

    async def verify_id_token(self, id_token: str, *, nonce: str) -> dict[str, Any]:
        try:
            decoded = jwt.decode(id_token, await self._keyset(), algorithms=ALGORITHMS)
        except (JoseError, ValueError) as first_error:
            # Most likely a key rotation: refetch the JWKS (rate limited) and
            # try once more before giving up.
            logger.info("id_token did not verify against the cached JWKS (%s); refetching", first_error)
            try:
                decoded = jwt.decode(id_token, await self._keyset(refresh=True), algorithms=ALGORITHMS)
            except (JoseError, ValueError) as e:
                raise OIDCError(f"id_token signature invalid: {e}") from e

        registry = jwt.JWTClaimsRegistry(
            leeway=30,
            iss={"essential": True, "value": self._settings.issuer},
            aud={"essential": True, "value": self._settings.KC_CLIENT_ID},
            nonce={"essential": True, "value": nonce},
            exp={"essential": True},
            sub={"essential": True},
        )
        try:
            registry.validate(decoded.claims)
        except JoseError as e:
            raise OIDCError(f"id_token claims invalid: {e}") from e
        return dict(decoded.claims)
