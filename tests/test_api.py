"""The /api routes, against an in-memory Keycloak."""

import pytest
from conftest import PUBLIC_URL, make_principal

from app.authz import OWNER_ATTRIBUTE
from app.session import SESSION_COOKIE, get_store

KAR = {"784": "Trollbäckens Scoutkår"}


def ids(response):
    return sorted(c["clientId"] for c in response.json())


# --- /me ---


def test_me_without_session_is_401(api):
    assert api.get("/api/me").status_code == 401


def test_me_answers_without_access_so_the_spa_can_explain(api):
    response = api.as_user().get("/api/me")
    assert response.status_code == 200
    assert response.json()["hasAccess"] is False


def test_routes_other_than_me_need_access(api):
    assert api.as_user().get("/api/clients").status_code == 403


# --- Listing and reading ---


def test_it_manager_lists_only_own_clients(api):
    assert ids(api.as_user(groups=KAR).get("/api/clients")) == ["784-wiki"]


def test_admin_lists_everything(api, clients):
    assert ids(api.as_user(admin=True).get("/api/clients")) == sorted(clients)


def test_it_manager_gets_404_for_another_kars_client(api, clients):
    response = api.as_user(groups=KAR).get(f"/api/clients/{clients['999-wiki']['id']}")
    assert response.status_code == 404


def test_it_manager_gets_404_for_ownerless_client(api, clients):
    response = api.as_user(groups=KAR).get(f"/api/clients/{clients['legacy.example.test']['id']}")
    assert response.status_code == 404


def test_malformed_client_id_never_reaches_keycloak(api):
    assert api.as_user(admin=True).get("/api/clients/..%2Fusers").status_code in (404, 422)


def test_detail_shows_owner_and_post_logout_uris(api, kc, clients):
    kc.clients[clients["784-wiki"]["id"]]["attributes"]["post.logout.redirect.uris"] = "+##https://x.se/bye"
    body = api.as_user(groups=KAR).get(f"/api/clients/{clients['784-wiki']['id']}").json()
    assert body["owner"] == "784"
    assert body["postLogoutRedirectUris"] == ["+", "https://x.se/bye"]


# --- Creating ---


def test_it_manager_creates_client_for_own_kar(api, kc):
    response = api.as_user(groups=KAR).post(
        "/api/clients", json={"preset": "webapp", "owner": "784", "clientId": "784-photos", "domain": "photos.se"}
    )
    assert response.status_code == 201
    body = response.json()
    assert body["secret"] == "secret-of-784-photos"
    assert kc.clients[body["id"]]["attributes"][OWNER_ATTRIBUTE] == "784"


def test_it_manager_cannot_create_for_another_kar(api, kc):
    response = api.as_user(groups=KAR).post(
        "/api/clients", json={"preset": "webapp", "owner": "999", "clientId": "999-photos", "domain": "photos.se"}
    )
    assert response.status_code == 400
    assert not [c for c in kc.calls if c[0] == "create"]


def test_create_ignores_a_smuggled_representation(api, kc):
    # Extra fields are not part of CreateRequest and must not reach Keycloak.
    response = api.as_user(groups=KAR).post(
        "/api/clients",
        json={
            "preset": "service",
            "owner": "784",
            "clientId": "784-svc",
            "protocolMappers": [{"name": "evil"}],
            "attributes": {OWNER_ATTRIBUTE: "999"},
        },
    )
    assert response.status_code == 201
    created = kc.clients[response.json()["id"]]
    assert "protocolMappers" not in created
    assert created["attributes"][OWNER_ATTRIBUTE] == "784"


def test_duplicate_client_id_is_reported_as_conflict(api):
    response = api.as_user(groups=KAR).post(
        "/api/clients", json={"preset": "service", "owner": "784", "clientId": "784-wiki"}
    )
    assert response.status_code == 409


def test_public_client_has_no_secret(api):
    response = api.as_user(groups=KAR).post(
        "/api/clients", json={"preset": "spa", "owner": "784", "clientId": "784-spa", "domain": "spa.se"}
    )
    assert response.json()["secret"] is None


def test_preview_returns_payload_and_derived_values(api):
    response = api.as_user(groups=KAR).post(
        "/api/clients/preview", json={"preset": "karwebb", "owner": "784", "domain": "https://minkar.se/wp/"}
    )
    assert response.status_code == 200
    body = response.json()
    assert body["clientId"] == "minkar.se/wp"
    assert body["endpoint"] == "https://minkar.se/wp/wp-login.php"


# --- Updating ---


def test_it_manager_updates_whitelisted_fields(api, kc, clients):
    id = clients["784-wiki"]["id"]
    response = api.as_user(groups=KAR).put(
        f"/api/clients/{id}",
        json={"name": "Wiki", "redirectUris": ["https://wiki.se/*"], "postLogoutRedirectUris": ["+"]},
    )
    assert response.status_code == 200
    assert kc.clients[id]["name"] == "Wiki"
    assert kc.clients[id]["attributes"]["post.logout.redirect.uris"] == "+"


def test_update_ignores_fields_outside_the_whitelist(api, kc, clients):
    id = clients["784-wiki"]["id"]
    api.as_user(groups=KAR).put(
        f"/api/clients/{id}",
        json={"clientId": "999-wiki", "serviceAccountsEnabled": True, "attributes": {OWNER_ATTRIBUTE: "999"}},
    )
    assert kc.clients[id]["clientId"] == "784-wiki"
    assert kc.clients[id]["serviceAccountsEnabled"] is False
    assert kc.clients[id]["attributes"][OWNER_ATTRIBUTE] == "784"


def test_it_manager_cannot_change_owner(api, kc, clients):
    id = clients["784-wiki"]["id"]
    response = api.as_user(groups={**KAR, "999": "Other"}).put(f"/api/clients/{id}", json={"owner": "999"})
    assert response.status_code == 403
    assert kc.clients[id]["attributes"][OWNER_ATTRIBUTE] == "784"


def test_it_manager_cannot_set_wildcard_redirect(api, kc, clients):
    id = clients["784-wiki"]["id"]
    response = api.as_user(groups=KAR).put(f"/api/clients/{id}", json={"redirectUris": ["*"]})
    assert response.status_code == 400
    assert ("update", id) not in kc.calls


def test_it_manager_cannot_update_another_kars_client(api, kc, clients):
    id = clients["999-wiki"]["id"]
    response = api.as_user(groups=KAR).put(f"/api/clients/{id}", json={"name": "mine now"})
    assert response.status_code == 404
    assert ("update", id) not in kc.calls


def test_admin_assigns_and_removes_owner(api, kc, clients):
    id = clients["legacy.example.test"]["id"]
    admin = api.as_user(admin=True)
    assert admin.put(f"/api/clients/{id}", json={"owner": "784"}).json()["owner"] == "784"
    # Handed over: the kår's IT manager now sees it.
    assert "legacy.example.test" in ids(api.as_user(groups=KAR).get("/api/clients"))
    assert api.as_user(admin=True).put(f"/api/clients/{id}", json={"owner": ""}).json()["owner"] is None


@pytest.mark.parametrize("client_id", ["account", "scoutid-admin-gui"])
def test_protected_clients_are_read_only_even_for_admins(api, kc, clients, client_id):
    id = clients[client_id]["id"]
    admin = api.as_user(admin=True)
    assert admin.get(f"/api/clients/{id}").status_code == 200
    assert admin.put(f"/api/clients/{id}", json={"name": "x"}).status_code == 403
    assert admin.delete(f"/api/clients/{id}").status_code == 403
    assert id in kc.clients


# --- Deleting, secrets, scopes ---


def test_it_manager_deletes_own_client(api, kc, clients):
    id = clients["784-wiki"]["id"]
    assert api.as_user(groups=KAR).delete(f"/api/clients/{id}").status_code == 204
    assert id not in kc.clients


def test_it_manager_cannot_delete_another_kars_client(api, kc, clients):
    id = clients["999-wiki"]["id"]
    assert api.as_user(groups=KAR).delete(f"/api/clients/{id}").status_code == 404
    assert id in kc.clients


def test_secret_of_own_client(api, clients):
    response = api.as_user(groups=KAR).get(f"/api/clients/{clients['784-wiki']['id']}/secret")
    assert response.json() == {"value": "secret-of-784-wiki"}


def test_secret_of_another_kars_client_is_404(api, clients):
    assert api.as_user(groups=KAR).get(f"/api/clients/{clients['999-wiki']['id']}/secret").status_code == 404


def test_memberships_scope_toggle(api, kc, clients):
    id = clients["784-wiki"]["id"]
    user = api.as_user(groups=KAR)
    assert user.put(f"/api/clients/{id}/memberships-scope").status_code == 204
    assert user.get(f"/api/clients/{id}").json()["memberships"] is True
    assert user.delete(f"/api/clients/{id}/memberships-scope").status_code == 204
    assert user.get(f"/api/clients/{id}").json()["memberships"] is False


def test_memberships_scope_missing_from_realm(api, kc, clients):
    kc.has_membership_scope = False
    response = api.as_user(groups=KAR).put(f"/api/clients/{clients['784-wiki']['id']}/memberships-scope")
    assert response.status_code == 409


# --- CSRF ---


@pytest.mark.parametrize(
    "headers",
    [
        {"X-Requested-With": "", "Origin": PUBLIC_URL},
        {"X-Requested-With": "scoutid-admin", "Origin": "https://evil.example"},
    ],
)
def test_mutations_need_the_csrf_header_and_our_origin(api, kc, clients, headers):
    id = clients["784-wiki"]["id"]
    response = api.as_user(groups=KAR).delete(f"/api/clients/{id}", headers=headers)
    assert response.status_code == 403
    assert id in kc.clients


def test_reads_do_not_need_the_csrf_header(api):
    assert api.as_user(groups=KAR).get("/api/clients", headers={"X-Requested-With": ""}).status_code == 200


# --- Real sessions (no require_principal override) ---


def test_session_cookie_resolves_to_its_principal(api):
    session_id = get_store().create(make_principal(groups=KAR), None)
    api.cookies.set(SESSION_COOKIE, session_id)
    assert api.get("/api/me").json()["groups"] == KAR


@pytest.mark.parametrize("session_id", ["", "made-up"])
def test_unknown_session_is_401(api, session_id):
    api.cookies.set(SESSION_COOKIE, session_id)
    assert api.get("/api/me").status_code == 401


def test_expired_session_is_401(api):
    store = get_store()
    session_id = store.create(make_principal(groups=KAR), None)
    store._sessions[session_id].expires_at = 0
    api.cookies.set(SESSION_COOKIE, session_id)
    assert api.get("/api/me").status_code == 401


# --- The SPA ---


def test_spa_routes_fall_back_to_index(api):
    response = api.get("/some/client/route")
    assert response.status_code == 200
    assert "<title>SPA</title>" in response.text


def test_assets_are_served_with_long_cache(api):
    response = api.get("/assets/app-1234.js")
    assert response.status_code == 200
    assert "immutable" in response.headers["Cache-Control"]


@pytest.mark.parametrize("path", ["/../secret.txt", "/%2e%2e/secret.txt", "/assets/../../secret.txt"])
def test_files_outside_the_spa_are_never_served(api, path):
    assert "do not serve" not in api.get(path).text


def test_unknown_api_path_is_404_not_the_spa(api):
    response = api.as_user(admin=True).get("/api/nope")
    assert response.status_code == 404
    assert "<title>SPA</title>" not in response.text


def test_security_headers(api):
    response = api.get("/")
    assert "frame-ancestors 'none'" in response.headers["Content-Security-Policy"]
    assert response.headers["Cache-Control"] == "no-store"


def test_healthz(api):
    assert api.get("/healthz").json() == {"status": "ok"}
