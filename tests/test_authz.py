"""The permission rules. Most cases here are ones where a plausible implementation
would grant too much: a role id in the wrong place, a near-miss prefix, a URI
that looks concrete but is not."""

import json

import pytest
from conftest import client_rep, make_principal

from app.authz import (
    PolicyError,
    can_access,
    check_client_id,
    check_owner,
    check_uris,
    is_protected,
    principal_from_claims,
)
from app.config import get_settings

settings = get_settings()

IT_MANAGER = {"id": 136, "key": "it_manager", "name": "IT Manager"}
OTHER_ROLE = {"id": 4, "key": "vice_leader", "name": "Vice ledare"}
NATIONAL_ADMIN = {"id": 235, "key": "something"}


def claims(memberships, **extra):
    return {"sub": "s", "preferred_username": "1234567@scoutnet", "name": "Test", "memberships": memberships, **extra}


# --- principal_from_claims ---


def test_it_manager_of_a_group_gets_that_group():
    p = principal_from_claims(
        claims({"groups": {"784": {"name": "Trollbäckens Scoutkår", "roles": [OTHER_ROLE, IT_MANAGER]}}}), settings
    )
    assert p.groups == {"784": "Trollbäckens Scoutkår"}
    assert not p.is_admin
    assert p.has_access


def test_only_groups_with_the_it_manager_role_count():
    p = principal_from_claims(
        claims(
            {
                "groups": {
                    "784": {"name": "A", "roles": [IT_MANAGER]},
                    "785": {"name": "B", "roles": [OTHER_ROLE]},
                }
            }
        ),
        settings,
    )
    assert p.groups == {"784": "A"}


def test_role_235_in_the_admin_organisation_makes_admin():
    p = principal_from_claims(claims({"organisations": {"692": {"roles": [NATIONAL_ADMIN]}}}), settings)
    assert p.is_admin
    assert p.has_access


@pytest.mark.parametrize(
    "memberships",
    [
        # Right role, wrong organisation.
        {"organisations": {"111": {"roles": [NATIONAL_ADMIN]}}},
        # Right role id, but held in a group rather than the organisation.
        {"groups": {"692": {"name": "x", "roles": [NATIONAL_ADMIN]}}},
        # it_manager in the admin organisation is not the admin role.
        {"organisations": {"692": {"roles": [IT_MANAGER]}}},
        # it_manager in a troop is not it_manager of a kår.
        {"troops": {"12345": {"name": "x", "groupId": 784, "roles": [IT_MANAGER]}}},
    ],
)
def test_near_misses_grant_nothing(memberships):
    p = principal_from_claims(claims(memberships), settings)
    assert not p.is_admin
    assert p.groups == {}
    assert not p.has_access


def test_role_ids_given_as_strings_are_accepted():
    p = principal_from_claims(claims({"groups": {"784": {"name": "A", "roles": [{"id": "136"}]}}}), settings)
    assert p.groups == {"784": "A"}


@pytest.mark.parametrize(
    "memberships",
    [None, "not json", [], {"groups": "nope"}, {"groups": {"784": {"roles": [{"id": "x"}, {"key": "k"}, None]}}}],
)
def test_malformed_memberships_grant_nothing_and_do_not_crash(memberships):
    p = principal_from_claims(claims(memberships), settings)
    assert not p.has_access


def test_memberships_as_a_json_string_is_parsed():
    p = principal_from_claims(claims(json.dumps({"organisations": {"692": {"roles": [NATIONAL_ADMIN]}}})), settings)
    assert p.is_admin


def test_truncated_memberships_are_reported():
    p = principal_from_claims(
        claims({"groups": {}, "error": "memberships_truncated: payload exceeded 2048 chars"}), settings
    )
    assert p.memberships_error and "truncated" in p.memberships_error
    assert not p.has_access


def test_admin_client_role_is_ignored_unless_configured():
    c = claims({}, resource_access={"scoutid-admin-gui": {"roles": ["admin"]}})
    assert not principal_from_claims(c, settings).is_admin
    configured = settings.model_copy(update={"ADMIN_CLIENT_ROLE": "admin"})
    assert principal_from_claims(c, configured).is_admin


def test_admin_client_role_on_another_client_does_not_count():
    configured = settings.model_copy(update={"ADMIN_CLIENT_ROLE": "admin"})
    c = claims({}, resource_access={"some-other-client": {"roles": ["admin"]}})
    assert not principal_from_claims(c, configured).is_admin


# --- can_access / is_protected ---


@pytest.mark.parametrize(
    ("owner", "groups", "allowed"),
    [
        ("784", {"784": "A"}, True),
        ("999", {"784": "A"}, False),
        # Prefix-looking but different group ids.
        ("7845", {"784": "A"}, False),
        ("78", {"784": "A"}, False),
        # Unowned clients are admin-only.
        (None, {"784": "A"}, False),
    ],
)
def test_it_manager_access_follows_the_owner_attribute(owner, groups, allowed):
    assert can_access(make_principal(groups=groups), client_rep("x", owner=owner)) is allowed


def test_client_id_prefix_alone_does_not_grant_access():
    # Named like a 784 client, but owned by nobody.
    assert not can_access(make_principal(groups={"784": "A"}), client_rep("784-sneaky"))


def test_admin_can_access_everything():
    assert can_access(make_principal(admin=True), client_rep("anything"))


@pytest.mark.parametrize(
    ("client_id", "protected"),
    [("account", True), ("realm-management", True), ("scoutid-admin-gui", True), ("784-wiki", False)],
)
def test_protected_clients(client_id, protected):
    assert is_protected(client_rep(client_id), settings) is protected


# --- check_owner ---


def test_it_manager_must_name_one_of_their_groups():
    p = make_principal(groups={"784": "A"})
    check_owner(p, "784")
    with pytest.raises(PolicyError):
        check_owner(p, "999")
    with pytest.raises(PolicyError):
        check_owner(p, None)


def test_admin_may_name_any_group_or_none_but_only_numbers():
    p = make_principal(admin=True)
    check_owner(p, "999")
    check_owner(p, None)
    with pytest.raises(PolicyError):
        check_owner(p, "abc")


# --- check_client_id ---


@pytest.mark.parametrize(
    ("client_id", "owner", "protocol", "ok"),
    [
        ("784-wiki", "784", "openid-connect", True),
        ("784-", "784", "openid-connect", False),
        ("7840-wiki", "784", "openid-connect", False),
        ("wiki", "784", "openid-connect", False),
        ("999-wiki", "784", "openid-connect", False),
        # SAML clientIds are entity IDs and keep whatever the SP uses.
        ("minkar.se/wp", "784", "saml", True),
        # Without an owner (admins only), any name goes.
        ("wiki", None, "openid-connect", True),
        ("", None, "openid-connect", False),
    ],
)
def test_client_id_prefix(client_id, owner, protocol, ok):
    if ok:
        check_client_id(client_id, owner, protocol)
    else:
        with pytest.raises(PolicyError):
            check_client_id(client_id, owner, protocol)


# --- check_uris ---


@pytest.mark.parametrize(
    "uri",
    [
        "https://minkar.se/*",
        "https://minkar.se/wp/wp-login.php",
        "https://minkar.se:8443/cb",
        "http://localhost:3000/*",
        "http://127.0.0.1/cb",
    ],
)
def test_concrete_redirect_uris_are_accepted(uri):
    check_uris(make_principal(groups={"784": "A"}), redirect_uris=[uri])


@pytest.mark.parametrize(
    "uri",
    [
        "*",
        "/*",
        "https://*",
        "https://*.se/*",
        "https://*.minkar.se/*",
        "http://minkar.se/*",
        "javascript:alert(1)",
        "https://",
        "http://localhost.evil.se/*",
    ],
)
def test_wildcard_and_insecure_redirect_uris_are_refused(uri):
    with pytest.raises(PolicyError):
        check_uris(make_principal(groups={"784": "A"}), redirect_uris=[uri])


def test_admin_uris_are_not_restricted():
    check_uris(make_principal(admin=True), redirect_uris=["*"], web_origins=["*"])


@pytest.mark.parametrize(("origin", "ok"), [("https://minkar.se", True), ("+", True), ("https://minkar.se/wp", False)])
def test_web_origins_are_origins(origin, ok):
    p = make_principal(groups={"784": "A"})
    if ok:
        check_uris(p, web_origins=[origin])
    else:
        with pytest.raises(PolicyError):
            check_uris(p, web_origins=[origin])


def test_plus_means_same_as_redirect_uris_for_post_logout():
    check_uris(make_principal(groups={"784": "A"}), post_logout_uris=["+"])
