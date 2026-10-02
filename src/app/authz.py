"""Who may do what, derived from the Scoutnet `memberships` claim.

Users of this GUI hold no realm-management roles of their own: every Admin API
call is made by the backend's service account, so these rules are the only
thing standing between a kår's IT manager and every other kår's clients. Keep
them small, pure and tested.
"""

import json
import logging
from collections.abc import Sequence
from typing import Any
from urllib.parse import urlsplit

from pydantic import BaseModel

from .config import Settings

logger = logging.getLogger(__name__)

# The kår that owns a client. Stored on the client itself so it survives
# renames and works for SAML clients, whose clientId is an entity ID that cannot
# carry a prefix.
OWNER_ATTRIBUTE = "scoutid.owner.group"

# Clients Keycloak creates for its own use. Read-only here even for admins:
# breaking one of them breaks the realm, and the console is the place for that.
BUILT_IN_CLIENT_IDS = frozenset(
    {
        "account",
        "account-console",
        "admin-cli",
        "broker",
        "realm-management",
        "security-admin-console",
    }
)


class Principal(BaseModel):
    sub: str
    username: str
    name: str
    is_admin: bool = False
    # Scoutnet group id -> group name, for every kår the user is IT manager of.
    groups: dict[str, str] = {}
    # Set when the provider had to truncate the memberships claim, which drops
    # `organisations` and so can silently cost an admin their rights.
    memberships_error: str | None = None

    @property
    def has_access(self) -> bool:
        return self.is_admin or bool(self.groups)

    def __str__(self) -> str:
        return f"{self.name} ({self.username})"


# --- Claims -> principal ---


def _role_ids(entry: Any) -> set[int]:
    """The numeric role ids of one memberships entry, ignoring malformed ones."""
    if not isinstance(entry, dict):
        return set()
    ids = set()
    for role in entry.get("roles") or []:
        try:
            ids.add(int(role["id"]))
        except KeyError, TypeError, ValueError:
            continue
    return ids


def _memberships(claims: dict[str, Any]) -> dict[str, Any]:
    raw = claims.get("memberships")
    if isinstance(raw, str):
        # The mapper is configured as JSON, but a String-typed mapper would hand
        # us the serialised form; accept both rather than lock everyone out.
        try:
            raw = json.loads(raw)
        except ValueError:
            logger.warning("Unparseable memberships claim for %s", claims.get("sub"))
            return {}
    return raw if isinstance(raw, dict) else {}


def principal_from_claims(claims: dict[str, Any], settings: Settings) -> Principal:
    memberships = _memberships(claims)

    organisations = memberships.get("organisations") or {}
    admin_entry = organisations.get(settings.ADMIN_ORG_ID) if isinstance(organisations, dict) else None
    is_admin = settings.ADMIN_ROLE_ID in _role_ids(admin_entry)

    if settings.ADMIN_CLIENT_ROLE:
        client_roles = ((claims.get("resource_access") or {}).get(settings.KC_CLIENT_ID) or {}).get("roles") or []
        is_admin = is_admin or settings.ADMIN_CLIENT_ROLE in client_roles

    groups: dict[str, str] = {}
    raw_groups = memberships.get("groups") or {}
    if isinstance(raw_groups, dict):
        for group_id, entry in raw_groups.items():
            if settings.GROUP_MANAGER_ROLE_ID in _role_ids(entry):
                groups[str(group_id)] = str(entry.get("name") or group_id)

    error = memberships.get("error")
    if error:
        logger.warning("memberships claim for %s carries an error: %s", claims.get("sub"), error)

    sub = str(claims.get("sub") or "")
    return Principal(
        sub=sub,
        username=str(claims.get("preferred_username") or sub),
        name=str(claims.get("name") or claims.get("preferred_username") or sub),
        is_admin=is_admin,
        groups=groups,
        memberships_error=str(error) if error else None,
    )


# --- Access to individual clients ---


def owner_of(client: dict[str, Any]) -> str | None:
    return (client.get("attributes") or {}).get(OWNER_ATTRIBUTE) or None


def is_protected(client: dict[str, Any], settings: Settings) -> bool:
    client_id = client.get("clientId")
    return client_id in BUILT_IN_CLIENT_IDS or client_id == settings.KC_CLIENT_ID


def can_access(principal: Principal, client: dict[str, Any]) -> bool:
    if principal.is_admin:
        return True
    owner = owner_of(client)
    return owner is not None and owner in principal.groups


# --- Validation of what a user asks for ---


class PolicyError(ValueError):
    """A request the caller is not allowed to make. The message is shown to them."""


def check_owner(principal: Principal, owner: str | None) -> None:
    if owner is not None and not owner.isdigit():
        raise PolicyError("Kår-ID ska vara ett nummer, t.ex. 784.")
    if principal.is_admin:
        return
    if owner is None:
        raise PolicyError("Välj vilken kår klienten tillhör.")
    if owner not in principal.groups:
        raise PolicyError(f"Du är inte IT-ansvarig för kår {owner}.")


def check_client_id(client_id: str, owner: str | None, protocol: str) -> None:
    """OIDC clients owned by a kår are named <kår>-<something>.

    The prefix keeps kårer from colliding on names like "wordpress". SAML
    clients are exempt: their clientId is the SP's entity ID, which the SP
    dictates.
    """
    if not client_id.strip():
        raise PolicyError("Client ID krävs.")
    if owner is None or protocol == "saml":
        return
    prefix = f"{owner}-"
    if not client_id.startswith(prefix) or len(client_id) == len(prefix):
        raise PolicyError(f"Client ID måste börja med {prefix} följt av ett namn.")


_LOCAL_HOSTS = frozenset({"localhost", "127.0.0.1", "[::1]"})


def _check_uri(uri: str, *, origin_only: bool) -> None:
    parts = urlsplit(uri)
    host = parts.hostname or ""
    local = parts.scheme == "http" and host in _LOCAL_HOSTS
    if parts.scheme != "https" and not local:
        raise PolicyError(f"{uri}: måste börja med https://")
    if not host or "*" in parts.netloc:
        raise PolicyError(f"{uri}: värdnamnet måste anges utan jokertecken")
    if origin_only and (parts.path not in ("", "/") or parts.query or parts.fragment):
        raise PolicyError(f"{uri}: web origin får bara innehålla schema och värd")


def check_uris(
    principal: Principal,
    *,
    redirect_uris: Sequence[str] = (),
    post_logout_uris: Sequence[str] = (),
    web_origins: Sequence[str] = (),
) -> None:
    """Keep IT managers to concrete https endpoints.

    A bare "*" or a wildcard host would let the client receive codes for any
    site. Admins are trusted to know when they need something unusual, and
    Keycloak still validates the syntax.
    """
    if principal.is_admin:
        return
    for uri in redirect_uris:
        _check_uri(uri, origin_only=False)
    for uri in post_logout_uris:
        # "+" means "same as the redirect URIs", which are checked above.
        if uri != "+":
            _check_uri(uri, origin_only=False)
    for uri in web_origins:
        if uri != "+":
            _check_uri(uri, origin_only=True)
