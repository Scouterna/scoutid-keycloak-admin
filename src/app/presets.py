"""Client presets — the reason this GUI exists rather than sending admins to the
generic Keycloak console. Each preset expands a domain (or a kår id) into the
endpoints and flags that kind of integration needs, the same way the old ScoutID
admin turned a domain into a Service Provider.

They live on the server, not in the SPA, because the server must not accept a
ClientRepresentation from the browser: an IT manager could otherwise send any
mapper or attribute they like. The browser names a preset and fills in a few
fields; everything else is decided here.

How ScoutID clients should be configured is documented in
docs/client_config_guide.md in scoutid-keycloak-provider. Where the two
disagree, the guide is right.
"""

import re
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Literal

from pydantic import BaseModel

from .authz import OWNER_ATTRIBUTE, PolicyError, Principal, check_client_id, check_owner, check_uris

# Identity scopes every OIDC client gets: who the user is, nothing more.
SCOUTID_OIDC_SCOPES = ["profile", "email", "phone"]

# Organisational data: primary group, and every group membership with the roles
# held in it (it_manager, member_registrar, …). Opt-in, for two reasons:
#
# - It is personal data beyond identity. A client that only needs to know *who*
#   someone is should not receive their position in the organisation.
# - It roughly doubles the token. The memberships and group_emails_json claims
#   are JSON blobs that grow with the number of groups and roles a person holds.
#
# Kårwebbar need it — that is how a group's IT managers are recognised.
MEMBERSHIP_SCOPE = "scoutnet-memberships"

# Keycloak stores post-logout redirect URIs as one "##"-separated attribute.
POST_LOGOUT_ATTRIBUTE = "post.logout.redirect.uris"


@dataclass(frozen=True)
class BuildInput:
    client_id: str
    name: str
    domain: str
    endpoint: str
    memberships: bool
    kar_id: str


@dataclass(frozen=True)
class Preset:
    id: str
    label: str
    description: str
    protocol: Literal["openid-connect", "saml"]
    # Whether the form asks for a domain and expands it.
    needs_domain: bool
    build: Callable[[BuildInput], dict[str, Any]]
    # Whether the form asks for the kår's Scoutnet id (e.g. 766).
    needs_kar_id: bool = False
    # Endpoint derived from the domain, shown in the form so it can be corrected
    # before saving. None when the preset takes no domain.
    endpoint: Callable[[str], str] | None = None
    memberships_by_default: bool = False

    def metadata(self) -> dict[str, Any]:
        """What the create form needs to render this preset."""
        return {
            "id": self.id,
            "label": self.label,
            "description": self.description,
            "protocol": self.protocol,
            "needsDomain": self.needs_domain,
            "needsKarId": self.needs_kar_id,
            "hasEndpoint": self.endpoint is not None,
            "membershipsByDefault": self.memberships_by_default,
        }


def normalize_domain(raw: str) -> str:
    """Normalise a pasted domain, preserving a subdirectory when there is one.

    WordPress kårwebbar are commonly served from a /wp subdirectory: roughly
    half the legacy SAML SPs are keyed <host>/wp with their ACS at
    /wp/wp-login.php, the rest at the site root. Verified against a live login,
    whose SAMLRequest carries Issuer=<host>/wp and
    ACS=https://<host>/wp/wp-login.php.

    So a trailing path is meaningful, not noise — it becomes part of the entity
    ID and of every derived endpoint. Only the scheme, query, fragment and
    trailing slash are stripped.
    """
    domain = raw.strip()
    domain = re.sub(r"^https?://", "", domain)
    domain = re.sub(r"[?#].*$", "", domain)
    # Drop a trailing wp-login.php / wp-admin path, so pasting the address bar
    # of a logged-in kårwebb yields the site root rather than a nonsense entity.
    domain = re.sub(r"/wp-login\.php.*$", "", domain)
    domain = re.sub(r"/wp-admin(/.*)?$", "", domain)
    return domain.rstrip("/")


def origin_of(domain: str) -> str:
    """Host part only, for web origins (which cannot carry a path)."""
    return f"https://{domain.split('/')[0]}"


def _oidc_base(i: BuildInput) -> dict[str, Any]:
    return {
        "clientId": i.client_id,
        "name": i.name or i.client_id,
        "protocol": "openid-connect",
        "enabled": True,
        "defaultClientScopes": [*SCOUTID_OIDC_SCOPES, MEMBERSHIP_SCOPE] if i.memberships else SCOUTID_OIDC_SCOPES,
    }


def _saml_client(i: BuildInput) -> dict[str, Any]:
    acs = i.endpoint
    return {
        "clientId": i.client_id,
        "name": i.name or i.client_id,
        "protocol": "saml",
        "enabled": True,
        "frontchannelLogout": True,
        # No client scopes are set: attribute release for SAML clients is not
        # configured by this GUI. See docs/client_config_guide.md in
        # scoutid-keycloak-provider for how clients should be set up.
        "redirectUris": [acs],
        "adminUrl": acs,
        "attributes": {
            "saml_name_id_format": "email",
            "saml_assertion_consumer_url_post": acs,
            "saml_assertion_consumer_url_redirect": acs,
            "saml_single_logout_service_url_post": acs,
            # The legacy IdP signs assertions but does not require the SP to
            # sign its requests — the WordPress sites have no signing keys.
            "saml.assertion.signature": "true",
            "saml.server.signature": "true",
            "saml.client.signature": "false",
            "saml.authnstatement": "true",
            "saml_force_name_id_format": "true",
        },
    }


def _google_workspace(i: BuildInput) -> dict[str, Any]:
    return {
        "clientId": i.client_id,
        "name": i.name or i.client_id,
        "protocol": "openid-connect",
        "enabled": True,
        "publicClient": False,
        "standardFlowEnabled": True,
        "serviceAccountsEnabled": False,
        "rootUrl": "https://accounts.google.com",
        # Google issues the redirect URI only once the SSO profile exists, so
        # it is pasted in afterwards on the client's page.
        "redirectUris": [],
        # Deliberately no `email` scope: the built-in one would emit an `email`
        # claim from the user's personal address, colliding with the mapper
        # below that must supply the Workspace address instead.
        "defaultClientScopes": ["profile"],
        "protocolMappers": [
            {
                # The access restriction: group_email_<karId> exists only for
                # members of that kår (ScoutnetProfileSync sets it from the
                # group's `domain` attribute). A non-member gets no email claim,
                # so Google cannot match them to an account.
                "name": "group_email_mapper",
                "protocol": "openid-connect",
                "protocolMapper": "oidc-usermodel-attribute-mapper",
                "config": {
                    "user.attribute": f"group_email_{i.kar_id}",
                    "claim.name": "email",
                    "jsonType.label": "String",
                    "id.token.claim": "true",
                    "userinfo.token.claim": "true",
                    "lightweight.claim": "true",
                },
            }
        ],
    }


def _webapp(i: BuildInput) -> dict[str, Any]:
    return {
        **_oidc_base(i),
        "publicClient": False,
        "standardFlowEnabled": True,
        "serviceAccountsEnabled": False,
        "redirectUris": [i.endpoint],
        # Web origins are scheme+host only; a subdirectory is not valid here.
        "webOrigins": [origin_of(i.domain)],
        # "+" = same as the valid redirect URIs, so later edits stay in step.
        "attributes": {POST_LOGOUT_ATTRIBUTE: "+"},
    }


def _spa(i: BuildInput) -> dict[str, Any]:
    return {
        **_oidc_base(i),
        "publicClient": True,
        "standardFlowEnabled": True,
        "serviceAccountsEnabled": False,
        "redirectUris": [i.endpoint],
        "webOrigins": [origin_of(i.domain)],
        "attributes": {
            "pkce.code.challenge.method": "S256",
            POST_LOGOUT_ATTRIBUTE: "+",
        },
    }


def _service(i: BuildInput) -> dict[str, Any]:
    return {
        **_oidc_base(i),
        "publicClient": False,
        "standardFlowEnabled": False,
        "serviceAccountsEnabled": True,
        "redirectUris": [],
        "webOrigins": [],
    }


PRESETS: dict[str, Preset] = {
    p.id: p
    for p in [
        Preset(
            id="google-workspace",
            label="Google Workspace (OIDC)",
            description="SSO för en kårs Google Workspace. Följer client_config_guide.md i scoutid-keycloak-provider.",
            protocol="openid-connect",
            needs_domain=False,
            needs_kar_id=True,
            build=_google_workspace,
        ),
        Preset(
            id="karwebb",
            label="Kårwebb (SAML)",
            description="WordPress-based kårwebb. Matches how kårwebbar are integrated today.",
            protocol="saml",
            needs_domain=True,
            # Almost every legacy SP posts the assertion to wp-login.php. Sites
            # that serve WordPress from a /wp subdirectory use /wp/wp-login.php,
            # which normalize_domain keeps in the domain.
            endpoint=lambda domain: f"https://{domain}/wp-login.php",
            build=_saml_client,
        ),
        Preset(
            id="saml-generic",
            label="Övrig SAML-tjänst",
            description="Any other SAML service provider. Supply the ACS URL from its metadata.",
            protocol="saml",
            needs_domain=True,
            endpoint=lambda domain: f"https://{domain}/",
            build=_saml_client,
        ),
        Preset(
            id="webapp",
            label="Webbapplikation (OIDC)",
            description="Server-side web app that can keep a client secret (confidential).",
            protocol="openid-connect",
            needs_domain=True,
            endpoint=lambda domain: f"https://{domain}/*",
            build=_webapp,
        ),
        Preset(
            id="spa",
            label="SPA / frontend (OIDC)",
            description="Browser app using Authorization Code + PKCE. No client secret.",
            protocol="openid-connect",
            needs_domain=True,
            endpoint=lambda domain: f"https://{domain}/*",
            build=_spa,
        ),
        Preset(
            id="service",
            label="Backend-tjänst (OIDC)",
            description="Machine-to-machine client using client credentials. No user login.",
            protocol="openid-connect",
            needs_domain=False,
            build=_service,
        ),
    ]
}


def describe_client(client: dict[str, Any]) -> str:
    """Label for an existing client, for the list view's type column."""
    if client.get("protocol") == "saml":
        return "saml"
    if client.get("serviceAccountsEnabled") and not client.get("standardFlowEnabled"):
        return "service"
    return "public" if client.get("publicClient") else "confidential"


def has_secret(client: dict[str, Any]) -> bool:
    """True when the client has a secret an admin may need to copy."""
    return client.get("protocol") != "saml" and not client.get("publicClient")


# --- Building a client from a create request ---


class CreateRequest(BaseModel):
    preset: str
    # The kår that will own the client. Required for IT managers; an admin may
    # leave it out for clients that belong to no kår.
    owner: str | None = None
    # Empty means "derive it": the domain for SAML, <kår>-google-workspace for
    # Google Workspace. Required for the other presets.
    clientId: str = ""
    name: str = ""
    domain: str = ""
    # Empty means "derive it from the domain".
    endpoint: str = ""
    memberships: bool = False
    # Only for Google Workspace without an owner, i.e. admins.
    karId: str = ""


@dataclass(frozen=True)
class BuiltClient:
    payload: dict[str, Any]
    client_id: str
    domain: str
    endpoint: str


def build_client(principal: Principal, req: CreateRequest) -> BuiltClient:
    """Turn a create request into a ClientRepresentation, enforcing policy.

    Raises PolicyError, with a message meant for the user, when the request is
    incomplete or not allowed.
    """
    preset = PRESETS.get(req.preset)
    if preset is None:
        raise PolicyError(f"Okänd klienttyp: {req.preset}")

    owner = req.owner.strip() if req.owner and req.owner.strip() else None
    check_owner(principal, owner)

    domain = normalize_domain(req.domain)
    if preset.needs_domain and not domain:
        raise PolicyError("Domän krävs för den här klienttypen.")

    endpoint = req.endpoint.strip()
    if preset.endpoint is None:
        endpoint = ""
    elif not endpoint and domain:
        endpoint = preset.endpoint(domain)

    kar_id = ""
    if preset.needs_kar_id:
        # The group_email_<kår> mapper is the access restriction, so it must
        # name the owning kår — never one the user merely typed in.
        kar_id = owner or req.karId.strip()
        if not kar_id.isdigit():
            raise PolicyError("Kår-ID krävs och ska vara ett nummer, t.ex. 766.")

    client_id = req.clientId.strip()
    if not client_id:
        if preset.protocol == "saml":
            # The entity ID is the domain, including any /wp subdirectory,
            # matching how the legacy SPs are keyed.
            client_id = domain
        elif preset.needs_kar_id:
            client_id = f"{kar_id}-google-workspace"
    check_client_id(client_id, owner, preset.protocol)

    payload = preset.build(
        BuildInput(
            client_id=client_id,
            name=req.name.strip(),
            domain=domain,
            endpoint=endpoint,
            memberships=req.memberships and preset.protocol == "openid-connect",
            kar_id=kar_id,
        )
    )
    check_uris(
        principal,
        redirect_uris=payload.get("redirectUris", []),
        web_origins=payload.get("webOrigins", []),
    )
    if owner:
        payload.setdefault("attributes", {})[OWNER_ATTRIBUTE] = owner

    return BuiltClient(payload=payload, client_id=client_id, domain=domain, endpoint=endpoint)
