"""id_token verification and the service-account token cache, against httpx.MockTransport."""

import asyncio
import time

import httpx
import pytest
from joserfc import jwt
from joserfc.jwk import KeySet, RSAKey

from app.config import get_settings
from app.keycloak import KeycloakAdmin, KeycloakError
from app.oidc import OidcClient, OIDCError

settings = get_settings()

CERTS_URL = f"{settings.internal_realm_url}/protocol/openid-connect/certs"
TOKEN_URL = f"{settings.internal_realm_url}/protocol/openid-connect/token"


def run(coro):
    return asyncio.run(coro)


def new_key(kid: str) -> RSAKey:
    return RSAKey.generate_key(2048, parameters={"kid": kid, "alg": "RS256", "use": "sig"})


def sign(key: RSAKey, **overrides) -> str:
    now = int(time.time())
    claims = {
        "iss": settings.issuer,
        "aud": settings.KC_CLIENT_ID,
        "sub": "user-1",
        "nonce": "n-1",
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


def oidc_with(jwks: Jwks) -> OidcClient:
    return OidcClient(settings, httpx.AsyncClient(transport=httpx.MockTransport(jwks.handler)))


def test_valid_id_token_verifies():
    key = new_key("k1")
    claims = run(oidc_with(Jwks(key)).verify_id_token(sign(key), nonce="n-1"))
    assert claims["sub"] == "user-1"


@pytest.mark.parametrize(
    "overrides",
    [
        {"iss": "https://evil.example/realms/scoutid"},
        # The internal URL is not the issuer: tokens must carry the public one.
        {"iss": f"{settings.KC_INTERNAL_URL}/realms/scoutid"},
        {"aud": "some-other-client"},
        {"nonce": "n-other"},
        {"nonce": None},
        {"exp": int(time.time()) - 3600},
    ],
)
def test_wrong_claims_are_refused(overrides):
    key = new_key("k1")
    with pytest.raises(OIDCError):
        run(oidc_with(Jwks(key)).verify_id_token(sign(key, **overrides), nonce="n-1"))


def test_token_signed_by_an_unknown_key_is_refused():
    with pytest.raises(OIDCError):
        run(oidc_with(Jwks(new_key("k1"))).verify_id_token(sign(new_key("k1")), nonce="n-1"))


def test_key_rotation_refetches_the_jwks_once():
    old, new = new_key("old"), new_key("new")
    jwks = Jwks(old)
    oidc = oidc_with(jwks)

    async def scenario():
        await oidc.verify_id_token(sign(old), nonce="n-1")
        jwks.keys = [new]
        # Pretend the cache is old enough to be refreshed.
        oidc._jwks_fetched_at = 0
        return await oidc.verify_id_token(sign(new), nonce="n-1")

    assert run(scenario())["sub"] == "user-1"
    assert jwks.fetches == 2


def test_authorization_url_uses_public_host_and_public_url_callback():
    oidc = OidcClient(settings, httpx.AsyncClient())
    url = oidc.authorization_url(state="s", code_challenge="c", nonce="n")
    assert url.startswith(f"{settings.issuer}/protocol/openid-connect/auth?")
    assert "redirect_uri=https%3A%2F%2Fclients.example.test%2Fauth%2Fcallback" in url
    assert "code_challenge_method=S256" in url
    assert "scoutnet-memberships" in url


# --- Service-account token ---


class FakeKeycloak:
    def __init__(self) -> None:
        self.token_requests = 0
        self.reject_next = False

    def handler(self, request: httpx.Request) -> httpx.Response:
        if str(request.url) == TOKEN_URL:
            self.token_requests += 1
            assert request.headers["Authorization"].startswith("Basic ")
            return httpx.Response(200, json={"access_token": f"t{self.token_requests}", "expires_in": 300})
        assert str(request.url).startswith(settings.admin_api_url)
        if self.reject_next:
            self.reject_next = False
            return httpx.Response(401)
        if request.url.path.endswith("/clients"):
            return httpx.Response(200, json=[{"id": "1", "clientId": "x"}])
        return httpx.Response(404, json={"error": "Could not find client"})


def admin_with(fake: FakeKeycloak) -> KeycloakAdmin:
    return KeycloakAdmin(settings, httpx.AsyncClient(transport=httpx.MockTransport(fake.handler)))


def test_service_account_token_is_cached():
    fake = FakeKeycloak()
    kc = admin_with(fake)

    async def scenario():
        await kc.list_clients()
        await kc.list_clients()

    run(scenario())
    assert fake.token_requests == 1


def test_401_gets_one_retry_with_a_fresh_token():
    fake = FakeKeycloak()
    kc = admin_with(fake)
    fake.reject_next = True
    assert run(kc.list_clients()) == [{"id": "1", "clientId": "x"}]
    assert fake.token_requests == 2


def test_missing_client_is_none_and_other_errors_raise():
    kc = admin_with(FakeKeycloak())
    assert run(kc.get_client("00000000-0000-0000-0000-000000000000")) is None
    with pytest.raises(KeycloakError):
        run(kc.get_client_secret("00000000-0000-0000-0000-000000000000"))


def unreachable(request: httpx.Request) -> httpx.Response:
    raise httpx.ConnectError("All connection attempts failed", request=request)


def test_unreachable_keycloak_is_a_502_keycloak_error():
    kc = KeycloakAdmin(settings, httpx.AsyncClient(transport=httpx.MockTransport(unreachable)))
    with pytest.raises(KeycloakError) as e:
        run(kc.list_clients())
    assert e.value.status == 502


def test_unreachable_keycloak_fails_the_login_cleanly():
    oidc = OidcClient(settings, httpx.AsyncClient(transport=httpx.MockTransport(unreachable)))
    with pytest.raises(OIDCError):
        run(oidc.exchange_code(code="c", code_verifier="v"))
    with pytest.raises(OIDCError):
        run(oidc.verify_id_token(sign(new_key("k1")), nonce="n-1"))
