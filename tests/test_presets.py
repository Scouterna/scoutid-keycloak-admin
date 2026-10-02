"""Presets, and turning a create request into a ClientRepresentation."""

import pytest
from conftest import make_principal

from app.authz import OWNER_ATTRIBUTE, PolicyError
from app.presets import PRESETS, CreateRequest, build_client, describe_client, normalize_domain

IT_MANAGER = make_principal(groups={"784": "Trollbäckens Scoutkår"})
ADMIN = make_principal(admin=True)


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("minkar.se", "minkar.se"),
        ("https://minkar.se/", "minkar.se"),
        ("  http://minkar.se/wp/  ", "minkar.se/wp"),
        ("https://minkar.se/wp/wp-login.php?redirect_to=x", "minkar.se/wp"),
        ("https://minkar.se/wp-admin/options.php", "minkar.se"),
        ("minkar.se#top", "minkar.se"),
    ],
)
def test_normalize_domain(raw, expected):
    assert normalize_domain(raw) == expected


def test_every_preset_has_metadata():
    for preset in PRESETS.values():
        meta = preset.metadata()
        assert meta["id"] == preset.id
        assert meta["protocol"] in ("openid-connect", "saml")


def test_it_manager_client_gets_owner_attribute():
    built = build_client(IT_MANAGER, CreateRequest(preset="webapp", owner="784", clientId="784-wiki", domain="wiki.se"))
    assert built.payload["attributes"][OWNER_ATTRIBUTE] == "784"
    assert built.payload["redirectUris"] == ["https://wiki.se/*"]
    assert built.payload["webOrigins"] == ["https://wiki.se"]


def test_it_manager_cannot_create_for_another_kar():
    with pytest.raises(PolicyError):
        build_client(IT_MANAGER, CreateRequest(preset="webapp", owner="999", clientId="999-wiki", domain="wiki.se"))


def test_it_manager_cannot_create_without_owner():
    with pytest.raises(PolicyError):
        build_client(IT_MANAGER, CreateRequest(preset="webapp", clientId="wiki", domain="wiki.se"))


def test_it_manager_oidc_client_id_needs_the_prefix():
    with pytest.raises(PolicyError):
        build_client(IT_MANAGER, CreateRequest(preset="spa", owner="784", clientId="wiki", domain="wiki.se"))


def test_google_workspace_mapper_names_the_owner_not_the_typed_kar_id():
    # A typed karId must not let an IT manager read another kår's group_email_<id>.
    built = build_client(IT_MANAGER, CreateRequest(preset="google-workspace", owner="784", karId="999"))
    assert built.client_id == "784-google-workspace"
    mapper = built.payload["protocolMappers"][0]
    assert mapper["config"]["user.attribute"] == "group_email_784"


def test_admin_google_workspace_without_owner_uses_kar_id():
    built = build_client(ADMIN, CreateRequest(preset="google-workspace", karId="766"))
    assert built.client_id == "766-google-workspace"
    assert OWNER_ATTRIBUTE not in built.payload.get("attributes", {})


def test_saml_entity_id_is_derived_from_domain_and_keeps_subdirectory():
    built = build_client(IT_MANAGER, CreateRequest(preset="karwebb", owner="784", domain="https://minkar.se/wp/"))
    assert built.client_id == "minkar.se/wp"
    assert built.endpoint == "https://minkar.se/wp/wp-login.php"
    assert built.payload["attributes"]["saml_assertion_consumer_url_post"] == built.endpoint
    assert built.payload["attributes"][OWNER_ATTRIBUTE] == "784"


def test_saml_endpoint_from_it_manager_must_be_https():
    with pytest.raises(PolicyError):
        build_client(
            IT_MANAGER,
            CreateRequest(preset="saml-generic", owner="784", domain="minkar.se", endpoint="http://minkar.se/acs"),
        )


def test_domain_with_wildcard_host_is_refused_for_it_manager():
    with pytest.raises(PolicyError):
        build_client(IT_MANAGER, CreateRequest(preset="webapp", owner="784", clientId="784-x", domain="*.se"))


def test_memberships_scope_is_opt_in():
    without = build_client(ADMIN, CreateRequest(preset="webapp", clientId="x", domain="x.se"))
    with_ = build_client(ADMIN, CreateRequest(preset="webapp", clientId="x", domain="x.se", memberships=True))
    assert "scoutnet-memberships" not in without.payload["defaultClientScopes"]
    assert "scoutnet-memberships" in with_.payload["defaultClientScopes"]


def test_domain_required_where_the_preset_needs_one():
    with pytest.raises(PolicyError):
        build_client(ADMIN, CreateRequest(preset="webapp", clientId="x"))


def test_unknown_preset():
    with pytest.raises(PolicyError):
        build_client(ADMIN, CreateRequest(preset="nope", clientId="x"))


@pytest.mark.parametrize(
    ("client", "label"),
    [
        ({"protocol": "saml"}, "saml"),
        ({"serviceAccountsEnabled": True, "standardFlowEnabled": False}, "service"),
        ({"publicClient": True, "standardFlowEnabled": True}, "public"),
        ({"publicClient": False, "standardFlowEnabled": True}, "confidential"),
    ],
)
def test_describe_client(client, label):
    assert describe_client(client) == label
