"""Who is calling: the user's own Keycloak access token.

The SPA logs in against Keycloak itself (Authorization Code + PKCE) and sends
its access token with every /api call. The backend verifies the token, derives
the Scoutnet-based permissions from its claims, and forwards the *same token*
to the Admin API. Keycloak then applies its own check on top: the user must hold
realm-management roles (manage-clients), which admins assign by hand.

So two gates must both say yes: the Scoutnet rules here (which kår's clients),
and the Keycloak roles there (whether this person may manage clients at all).
Users never reach the Admin API directly, so the admin host can stay behind its
IP allowlist.
"""

import logging
import time
from typing import Any

import httpx
from fastapi import Depends, HTTPException, Request, status
from joserfc import jwt
from joserfc.errors import JoseError
from joserfc.jwk import KeySet

from .authz import Principal, principal_from_claims
from .config import Settings, get_settings
from .keycloak import KeycloakError

logger = logging.getLogger(__name__)

settings = get_settings()

ALGORITHMS = ["RS256"]
# At most one JWKS refetch per this many seconds, however many tokens with an
# unknown kid turn up.
JWKS_REFETCH_INTERVAL_SECONDS = 60


class TokenError(Exception):
    """An access token that must not be accepted."""


class TokenVerifier:
    def __init__(self, settings: Settings, http: httpx.AsyncClient) -> None:
        self._settings = settings
        self._http = http
        self._jwks: KeySet | None = None
        self._jwks_fetched_at = 0.0

    async def _keyset(self, *, refresh: bool = False) -> KeySet:
        stale = time.monotonic() - self._jwks_fetched_at > JWKS_REFETCH_INTERVAL_SECONDS
        if self._jwks is None or (refresh and stale):
            try:
                response = await self._http.get(f"{self._settings.internal_realm_url}/protocol/openid-connect/certs")
                response.raise_for_status()
            except httpx.HTTPError as e:
                # Not the token's fault: a 401 here would send the SPA into a
                # login loop during a Keycloak outage.
                logger.error("JWKS unavailable: %r", e)
                raise KeycloakError(502, "Kunde inte nå Keycloak.") from e
            self._jwks = KeySet.import_key_set(response.json())
            self._jwks_fetched_at = time.monotonic()
        return self._jwks

    async def verify(self, token: str) -> dict[str, Any]:
        try:
            decoded = jwt.decode(token, await self._keyset(), algorithms=ALGORITHMS)
        except (JoseError, ValueError) as first_error:
            # Most likely a key rotation: refetch the JWKS (rate limited) and
            # try once more before giving up.
            logger.info("Token did not verify against the cached JWKS (%s); refetching", first_error)
            try:
                decoded = jwt.decode(token, await self._keyset(refresh=True), algorithms=ALGORITHMS)
            except (JoseError, ValueError) as e:
                raise TokenError(f"signature invalid: {e}") from e

        registry = jwt.JWTClaimsRegistry(
            leeway=30,
            iss={"essential": True, "value": self._settings.issuer},
            # Keycloak access tokens name the client they were issued to in
            # azp; their aud is the resource servers (realm-management, …).
            azp={"essential": True, "value": self._settings.KC_CLIENT_ID},
            # An access token, not an id_token or refresh token.
            typ={"essential": True, "value": "Bearer"},
            exp={"essential": True},
            sub={"essential": True},
        )
        try:
            registry.validate(decoded.claims)
        except JoseError as e:
            raise TokenError(f"claims invalid: {e}") from e
        return dict(decoded.claims)


# --- Dependencies ---


def get_verifier(request: Request) -> TokenVerifier:
    return request.app.state.verifier


def bearer_token(request: Request) -> str:
    scheme, _, token = request.headers.get("Authorization", "").partition(" ")
    if scheme.lower() != "bearer" or not token:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Inte inloggad.")
    return token


async def require_principal(
    token: str = Depends(bearer_token), verifier: TokenVerifier = Depends(get_verifier)
) -> Principal:
    try:
        claims = await verifier.verify(token)
    except TokenError as e:
        logger.info("Refused token: %s", e)
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Inloggningen är ogiltig eller har gått ut.") from e
    return principal_from_claims(claims, settings)


def require_access(principal: Principal = Depends(require_principal)) -> Principal:
    if not principal.has_access:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Du saknar behörighet att administrera klienter.")
    return principal
