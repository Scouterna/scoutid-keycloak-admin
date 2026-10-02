"""The /auth routes, with the Keycloak side of the login faked."""

from urllib.parse import parse_qs, urlsplit

import pytest
from conftest import PUBLIC_URL
from fastapi.testclient import TestClient

from app.main import app
from app.oidc import OIDCError
from app.routes_auth import get_oidc
from app.session import SESSION_COOKIE, get_store

IT_MANAGER_CLAIMS = {
    "sub": "user-1",
    "preferred_username": "1234567@scoutnet",
    "name": "Test Testsson",
    "memberships": {"groups": {"784": {"name": "Trollbäckens Scoutkår", "roles": [{"id": 136}]}}},
}


class FakeOidc:
    """Stands in for OidcClient: real URL building, faked token exchange and verification."""

    def __init__(self) -> None:
        self.verified_nonces: list[str] = []
        self.fail = False

    def authorization_url(self, *, state, code_challenge, nonce):
        return f"https://id.example.test/auth?state={state}&nonce={nonce}&code_challenge={code_challenge}"

    def end_session_url(self, id_token):
        return f"https://id.example.test/logout?id_token_hint={id_token}"

    async def exchange_code(self, *, code, code_verifier):
        assert code == "the-code"
        assert code_verifier
        return {"id_token": "raw-id-token"}

    async def verify_id_token(self, id_token, *, nonce):
        if self.fail:
            raise OIDCError("bad signature")
        self.verified_nonces.append(nonce)
        return IT_MANAGER_CLAIMS


@pytest.fixture
def oidc():
    fake = FakeOidc()
    app.dependency_overrides[get_oidc] = lambda: fake
    try:
        yield fake
    finally:
        app.dependency_overrides.clear()


@pytest.fixture
def browser():
    return TestClient(app, base_url=PUBLIC_URL, follow_redirects=False)


def start_login(browser) -> dict[str, str]:
    response = browser.get("/auth/login")
    assert response.status_code == 302
    return {k: v[0] for k, v in parse_qs(urlsplit(response.headers["location"]).query).items()}


def test_login_round_trip_creates_a_session(browser, oidc):
    params = start_login(browser)
    response = browser.get("/auth/callback", params={"state": params["state"], "code": "the-code"})
    assert response.status_code == 302
    assert response.headers["location"] == f"{PUBLIC_URL}/"
    # The nonce sent to Keycloak is the one the id_token is checked against.
    assert oidc.verified_nonces == [params["nonce"]]

    cookie = response.cookies.get(SESSION_COOKIE)
    session = get_store().get(cookie)
    assert session is not None
    assert session.principal.groups == {"784": "Trollbäckens Scoutkår"}
    assert session.id_token == "raw-id-token"

    set_cookie = response.headers["set-cookie"].lower()
    assert "httponly" in set_cookie and "samesite=lax" in set_cookie and "secure" in set_cookie


def test_state_can_only_be_used_once(browser, oidc):
    params = start_login(browser)
    assert browser.get("/auth/callback", params={"state": params["state"], "code": "the-code"}).status_code == 302
    assert browser.get("/auth/callback", params={"state": params["state"], "code": "the-code"}).status_code == 400


def test_unknown_state_is_refused(browser, oidc):
    response = browser.get("/auth/callback", params={"state": "made-up", "code": "the-code"})
    assert response.status_code == 400
    assert SESSION_COOKIE not in response.cookies


def test_failed_verification_creates_no_session(browser, oidc):
    oidc.fail = True
    params = start_login(browser)
    response = browser.get("/auth/callback", params={"state": params["state"], "code": "the-code"})
    assert response.status_code == 400
    assert SESSION_COOKIE not in response.cookies


def test_keycloak_error_description_is_escaped(browser, oidc):
    response = browser.get(
        "/auth/callback", params={"error": "access_denied", "error_description": "<script>alert(1)</script>"}
    )
    assert response.status_code == 400
    assert "<script>" not in response.text
    assert "&lt;script&gt;" in response.text


def test_logout_ends_session_and_passes_id_token_hint(browser, oidc):
    params = start_login(browser)
    cookie = browser.get("/auth/callback", params={"state": params["state"], "code": "the-code"}).cookies[
        SESSION_COOKIE
    ]
    browser.cookies.set(SESSION_COOKIE, cookie)
    response = browser.get("/auth/logout")
    assert response.status_code == 302
    assert "id_token_hint=raw-id-token" in response.headers["location"]
    assert get_store().get(cookie) is None
