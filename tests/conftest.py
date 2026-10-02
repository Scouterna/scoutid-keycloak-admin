"""Shared fixtures.

Settings are read at import time, so the environment is set up here, before any
test module imports `app`. TestClient is deliberately not used as a context
manager: the lifespan (a real httpx client) never runs, and Keycloak and the
OIDC client are swapped in through dependency overrides instead.
"""

import copy
import os
import tempfile
import uuid
from pathlib import Path
from typing import Any

import pytest

# A built SPA stand-in, with a file outside it that must never be served.
_root = Path(tempfile.mkdtemp())
(_root / "static" / "assets").mkdir(parents=True)
(_root / "static" / "index.html").write_text("<!doctype html><title>SPA</title>")
(_root / "static" / "assets" / "app-1234.js").write_text("console.log('app')")
(_root / "secret.txt").write_text("do not serve")

os.environ.update(
    {
        "PUBLIC_URL": "https://clients.example.test",
        "KC_PUBLIC_URL": "https://id.example.test",
        "KC_INTERNAL_URL": "http://keycloak.internal:8080",
        "KC_REALM": "scoutid",
        "KC_CLIENT_ID": "scoutid-admin-gui",
        "KC_CLIENT_SECRET": "test-secret",
        "STATIC_DIR": str(_root / "static"),
    }
)
os.environ.pop("FAKE_USER_CLAIMS", None)

from fastapi.testclient import TestClient

from app.authz import OWNER_ATTRIBUTE, Principal
from app.keycloak import KeycloakError
from app.main import app
from app.routes_api import get_keycloak
from app.session import CSRF_HEADER, CSRF_HEADER_VALUE, require_principal

PUBLIC_URL = os.environ["PUBLIC_URL"]
MEMBERSHIP_SCOPE_ID = "scope-memberships"


def client_rep(client_id: str, owner: str | None = None, **fields: Any) -> dict[str, Any]:
    rep: dict[str, Any] = {
        "id": str(uuid.uuid4()),
        "clientId": client_id,
        "protocol": "openid-connect",
        "enabled": True,
        "publicClient": False,
        "standardFlowEnabled": True,
        "serviceAccountsEnabled": False,
        "redirectUris": [f"https://{client_id}.example.test/*"],
        "webOrigins": [],
        "defaultClientScopes": ["profile", "email"],
        "attributes": {},
    }
    if owner:
        rep["attributes"][OWNER_ATTRIBUTE] = owner
    rep.update(fields)
    return rep


class FakeKeycloakAdmin:
    """In-memory stand-in for KeycloakAdmin, applying updates the way Keycloak does."""

    def __init__(self, clients: list[dict[str, Any]]) -> None:
        self.clients = {c["id"]: copy.deepcopy(c) for c in clients}
        self.has_membership_scope = True
        self.calls: list[tuple[str, str]] = []

    async def list_clients(self) -> list[dict[str, Any]]:
        return copy.deepcopy(list(self.clients.values()))

    async def get_client(self, id: str) -> dict[str, Any] | None:
        return copy.deepcopy(self.clients.get(id))

    async def create_client(self, representation: dict[str, Any]) -> str:
        if any(c["clientId"] == representation["clientId"] for c in self.clients.values()):
            raise KeycloakError(409, f"Client {representation['clientId']} already exists")
        id = str(uuid.uuid4())
        self.clients[id] = {"id": id, "attributes": {}, **copy.deepcopy(representation)}
        self.calls.append(("create", id))
        return id

    async def update_client(self, id: str, representation: dict[str, Any]) -> None:
        client = self.clients[id]
        for key, value in representation.items():
            if key == "attributes":
                for name, attr in value.items():
                    # An empty value removes the attribute.
                    if attr == "":
                        client["attributes"].pop(name, None)
                    else:
                        client["attributes"][name] = attr
            else:
                client[key] = copy.deepcopy(value)
        self.calls.append(("update", id))

    async def delete_client(self, id: str) -> None:
        del self.clients[id]
        self.calls.append(("delete", id))

    async def get_client_secret(self, id: str) -> str:
        return f"secret-of-{self.clients[id]['clientId']}"

    async def find_client_scope(self, name: str) -> dict[str, Any] | None:
        if name == "scoutnet-memberships" and self.has_membership_scope:
            return {"id": MEMBERSHIP_SCOPE_ID, "name": name}
        return None

    async def get_default_client_scopes(self, id: str) -> list[dict[str, Any]]:
        return [{"name": n} for n in self.clients[id].get("defaultClientScopes", [])]

    async def add_default_client_scope(self, id: str, scope_id: str) -> None:
        assert scope_id == MEMBERSHIP_SCOPE_ID
        scopes = self.clients[id].setdefault("defaultClientScopes", [])
        if "scoutnet-memberships" not in scopes:
            scopes.append("scoutnet-memberships")
        self.calls.append(("add-scope", id))

    async def remove_default_client_scope(self, id: str, scope_id: str) -> None:
        assert scope_id == MEMBERSHIP_SCOPE_ID
        scopes = self.clients[id].setdefault("defaultClientScopes", [])
        if "scoutnet-memberships" in scopes:
            scopes.remove("scoutnet-memberships")
        self.calls.append(("remove-scope", id))


def make_principal(*, admin: bool = False, groups: dict[str, str] | None = None) -> Principal:
    return Principal(
        sub="sub-1",
        username="1234567@scoutnet",
        name="Test Testsson",
        is_admin=admin,
        groups=groups or {},
    )


@pytest.fixture
def clients() -> dict[str, dict[str, Any]]:
    """A realm with one client per kår, an ownerless one, and the protected built-ins."""
    reps = [
        client_rep("784-wiki", owner="784"),
        client_rep("999-wiki", owner="999"),
        client_rep("legacy.example.test", protocol="saml", redirectUris=["https://legacy.example.test/"]),
        client_rep("account", publicClient=True),
        client_rep("scoutid-admin-gui"),
    ]
    return {c["clientId"]: c for c in reps}


@pytest.fixture
def kc(clients) -> FakeKeycloakAdmin:
    return FakeKeycloakAdmin(list(clients.values()))


@pytest.fixture
def api(kc):
    """A TestClient that passes the CSRF check, with `api.as_user(...)` to pick who calls."""
    client = TestClient(app, base_url=PUBLIC_URL, headers={CSRF_HEADER: CSRF_HEADER_VALUE, "Origin": PUBLIC_URL})
    app.dependency_overrides[get_keycloak] = lambda: kc

    def as_user(*, admin: bool = False, groups: dict[str, str] | None = None) -> TestClient:
        principal = make_principal(admin=admin, groups=groups)
        app.dependency_overrides[require_principal] = lambda: principal
        return client

    client.as_user = as_user  # type: ignore[attr-defined]
    try:
        yield client
    finally:
        app.dependency_overrides.clear()
