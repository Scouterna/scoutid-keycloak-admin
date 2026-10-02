"""Access-token verification, and forwarding the user's token to the Admin API."""

import asyncio
import time

import httpx
import pytest
from conftest import PUBLIC_URL
from fastapi.testclient import TestClient
from joserfc import jwt
from joserfc.jwk import KeySet, RSAKey

from app.auth import TokenError, TokenVerifier, get_verifier
from app.config import get_settings
from app.keycloak import KeycloakAdmin, KeycloakError
from app.main import app

settings = get_settings()

CERTS_URL = f"{settings.internal_realm_url}/protocol/openid-connect/certs"

IT_MANAGER_MEMBERSHIPS = {"groups": {"784": {"name": "Trollbäckens Scoutkår", "roles": [{"id": 136}]}}}


def run(coro):
    return asyncio.run(coro)


def new_key(kid: str) -> RSAKey:
    return RSAKey.generate_key(2048, parameters={"kid": kid, "alg": "RS256", "use": "sig"})


def sign(key: RSAKey, **overrides) -> str:
    now = int(time.time())
    claims = {
        "iss": settings.issuer,
        "azp": settings.KC_CLIENT_ID,
        "aud": ["realm-management", "account"],
        "typ": "Bearer",
        "sub": "user-1",
        "preferred_username": "1234567@scoutnet",
        "name": "Test Testsson",
        "memberships": IT_MANAGER_MEMBERSHIPS,
        "iat": now,
        "exp": now + 300,
        **overrides,
    }
    claims = {k: v for k, v in claims.items() if v is not None}
    return jwt.encode({"alg": "RS256", "kid": key.kid}, claims, key)


class Jwks:
    """Serves whatever keys are current, counting fetches."""

    def __init__(self, *keys: RSAKey) -> None:
        self.keys = list(keys)
        self.fetches = 0

    def handler(self, request: httpx.Request) -> httpx.Response:
        assert str(request.url) == CERTS_URL
        self.fetches += 1
        return httpx.Response(200, json=KeySet(self.keys).as_dict(private=False))


def verifier_with(jwks: Jwks) -> TokenVerifier:
    return TokenVerifier(settings, httpx.AsyncClient(transport=httpx.MockTransport(jwks.handler)))


# --- TokenVerifier ---


def test_valid_access_token_verifies():
    key = new_key("k1")
    assert run(verifier_with(Jwks(key)).verify(sign(key)))["sub"] == "user-1"


@pytest.mark.parametrize(
    "overrides",
    [
        {"iss": "https://evil.example/realms/scoutid"},
        # The internal URL is not the issuer: tokens must carry the public one.
        {"iss": f"{settings.KC_INTERNAL_URL}/realms/scoutid"},
        # Issued to some other client of the realm.
        {"azp": "some-other-client"},
        {"azp": None},
        # An id_token or refresh token is not an access token.
        {"typ": "ID"},
        {"typ": "Refresh"},
        {"exp": int(time.time()) - 3600},
    ],
)
def test_wrong_claims_are_refused(overrides):
    key = new_key("k1")
    with pytest.raises(TokenError):
        run(verifier_with(Jwks(key)).verify(sign(key, **overrides)))


def test_token_signed_by_an_unknown_key_is_refused():
    with pytest.raises(TokenError):
        run(verifier_with(Jwks(new_key("k1"))).verify(sign(new_key("k1"))))


def test_key_rotation_refetches_the_jwks_once():
    old, new = new_key("old"), new_key("new")
    jwks = Jwks(old)
    verifier = verifier_with(jwks)

    async def scenario():
        await verifier.verify(sign(old))
        jwks.keys = [new]
        # Pretend the cache is old enough to be refreshed.
        verifier._jwks_fetched_at = 0
        return await verifier.verify(sign(new))

    assert run(scenario())["sub"] == "user-1"
    assert jwks.fetches == 2


def test_unreachable_jwks_is_a_keycloak_outage_not_a_bad_token():
    # A 401 would send the SPA back to login, in a loop, while Keycloak is down.
    def unreachable(request):
        raise httpx.ConnectError("down", request=request)

    verifier = TokenVerifier(settings, httpx.AsyncClient(transport=httpx.MockTransport(unreachable)))
    with pytest.raises(KeycloakError) as e:
        run(verifier.verify(sign(new_key("k1"))))
    assert e.value.status == 502


# --- A real token through the app (no require_principal override) ---


@pytest.fixture
def signed_in(kc):
    from app.routes_api import get_keycloak

    key = new_key("k1")
    verifier = verifier_with(Jwks(key))
    app.dependency_overrides[get_verifier] = lambda: verifier
    app.dependency_overrides[get_keycloak] = lambda: kc
    try:
        yield TestClient(app, base_url=PUBLIC_URL), key
    finally:
        app.dependency_overrides.clear()


def test_real_token_gives_the_scoutnet_permissions(signed_in):
    client, key = signed_in
    response = client.get("/api/me", headers={"Authorization": f"Bearer {sign(key)}"})
    assert response.status_code == 200
    assert response.json()["groups"] == {"784": "Trollbäckens Scoutkår"}


def test_real_token_lists_only_own_clients(signed_in):
    client, key = signed_in
    response = client.get("/api/clients", headers={"Authorization": f"Bearer {sign(key)}"})
    assert [c["clientId"] for c in response.json()] == ["784-wiki"]


@pytest.mark.parametrize("header", ["", "Bearer", "Basic dXNlcjpwdw==", "Bearer not-a-jwt"])
def test_missing_or_bad_token_is_401(signed_in, header):
    client, _ = signed_in
    assert client.get("/api/clients", headers={"Authorization": header}).status_code == 401


def test_expired_token_is_401(signed_in):
    client, key = signed_in
    token = sign(key, exp=int(time.time()) - 3600)
    assert client.get("/api/me", headers={"Authorization": f"Bearer {token}"}).status_code == 401


# --- KeycloakAdmin forwards the caller's token ---


def test_admin_api_gets_the_users_own_token():
    seen = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append((str(request.url), request.headers["Authorization"]))
        return httpx.Response(200, json=[])

    kc = KeycloakAdmin(settings, httpx.AsyncClient(transport=httpx.MockTransport(handler)), "the-users-token")
    run(kc.list_clients())
    assert seen == [(f"{settings.admin_api_url}/clients", "Bearer the-users-token")]


@pytest.mark.parametrize("status", [401, 403])
def test_keycloak_refusals_keep_their_status(status):
    kc = KeycloakAdmin(
        settings, httpx.AsyncClient(transport=httpx.MockTransport(lambda r: httpx.Response(status))), "t"
    )
    with pytest.raises(KeycloakError) as e:
        run(kc.list_clients())
    assert e.value.status == status


def test_unreachable_keycloak_is_a_502():
    def unreachable(request):
        raise httpx.ConnectError("down", request=request)

    kc = KeycloakAdmin(settings, httpx.AsyncClient(transport=httpx.MockTransport(unreachable)), "t")
    with pytest.raises(KeycloakError) as e:
        run(kc.list_clients())
    assert e.value.status == 502
