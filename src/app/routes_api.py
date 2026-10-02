"""/api: what the SPA calls. Every route enforces authz.py before Keycloak is touched.

Following wsj27-project-api, a client the caller may not see answers 404 rather
than 403, so its existence is not revealed. A client they may see but not change
(the protected built-ins, for admins) answers 403.
"""

import logging
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Path, Request, status
from pydantic import BaseModel

from .auth import bearer_token, require_access, require_principal
from .authz import (
    OWNER_ATTRIBUTE,
    Principal,
    can_access,
    check_owner,
    check_uris,
    is_protected,
    owner_of,
)
from .config import get_settings
from .keycloak import KeycloakAdmin
from .presets import (
    MEMBERSHIP_SCOPE,
    POST_LOGOUT_ATTRIBUTE,
    PRESETS,
    CreateRequest,
    build_client,
    describe_client,
    has_secret,
)

logger = logging.getLogger(__name__)

settings = get_settings()

# No CSRF check: requests authenticate with a bearer token the SPA adds itself,
# never with a cookie a browser would attach to a cross-site request.
router = APIRouter()

# Keycloak's internal client ids are UUIDs. Pinning the shape keeps a crafted id
# such as "../users" from steering the service account to another endpoint.
ClientId = Annotated[str, Path(pattern=r"^[0-9a-fA-F-]{36}$")]


def get_keycloak(request: Request, token: str = Depends(bearer_token)) -> KeycloakAdmin:
    """The Admin API, called with the caller's own token."""
    return KeycloakAdmin(settings, request.app.state.http, token)


# --- Helpers ---


def _endpoints(client: dict[str, Any]) -> list[str]:
    """SAML clients keep their ACS URL in attributes rather than redirectUris, so read both."""
    acs = (client.get("attributes") or {}).get("saml_assertion_consumer_url_post")
    uris = client.get("redirectUris") or []
    return [acs, *uris] if acs and acs not in uris else uris


def _summary(client: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": client["id"],
        "clientId": client["clientId"],
        "name": client.get("name") or "",
        "enabled": client.get("enabled", True),
        "type": describe_client(client),
        "owner": owner_of(client),
        "protected": is_protected(client, settings),
        "endpoints": _endpoints(client),
    }


def _detail(client: dict[str, Any]) -> dict[str, Any]:
    attributes = client.get("attributes") or {}
    post_logout = attributes.get(POST_LOGOUT_ATTRIBUTE) or ""
    return {
        **_summary(client),
        "protocol": client.get("protocol") or "openid-connect",
        "hasSecret": has_secret(client),
        "redirectUris": client.get("redirectUris") or [],
        "postLogoutRedirectUris": [u for u in post_logout.split("##") if u],
        "webOrigins": client.get("webOrigins") or [],
        "defaultClientScopes": client.get("defaultClientScopes") or [],
        "memberships": MEMBERSHIP_SCOPE in (client.get("defaultClientScopes") or []),
        "acsUrl": attributes.get("saml_assertion_consumer_url_post"),
    }


async def _load(kc: KeycloakAdmin, id: str, principal: Principal) -> dict[str, Any]:
    client = await kc.get_client(id)
    if client is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Klienten finns inte.")
    if not can_access(principal, client):
        logger.warning("Refused %s access to client %s (owner %s)", principal, client.get("clientId"), owner_of(client))
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Klienten finns inte.")
    return client


async def _load_mutable(kc: KeycloakAdmin, id: str, principal: Principal) -> dict[str, Any]:
    client = await _load(kc, id, principal)
    if is_protected(client, settings):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Keycloaks inbyggda klienter ändras i Keycloak-konsolen.")
    return client


def _audit(principal: Principal, action: str, client: dict[str, Any], **details: Any) -> None:
    extra = " ".join(f"{k}={v}" for k, v in details.items())
    logger.info(
        "AUDIT %s [%s] %s client %s owner=%s %s",
        principal,
        principal.sub,
        action,
        client.get("clientId"),
        owner_of(client) or "-",
        extra,
    )


# --- SPA bootstrap ---


@router.get("/config")
async def config() -> dict[str, str]:
    """Where the SPA logs in. Public: the SPA needs it before anyone is signed in."""
    return {"authority": settings.issuer, "clientId": settings.KC_CLIENT_ID}


# --- Who am I ---


@router.get("/me")
async def me(principal: Principal = Depends(require_principal)) -> dict[str, Any]:
    # Answers even without access, so the SPA can say *why* there is none
    # (e.g. a truncated memberships claim) instead of a bare 403.
    return {
        "name": principal.name,
        "username": principal.username,
        "isAdmin": principal.is_admin,
        "groups": principal.groups,
        "hasAccess": principal.has_access,
        "membershipsError": principal.memberships_error,
    }


# --- Presets and create ---


@router.get("/presets")
async def presets(_: Principal = Depends(require_access)) -> list[dict[str, Any]]:
    return [p.metadata() for p in PRESETS.values()]


@router.post("/clients/preview")
async def preview(req: CreateRequest, principal: Principal = Depends(require_access)) -> dict[str, Any]:
    """What POST /clients would send to Keycloak, plus the values derived on the way."""
    built = build_client(principal, req)
    return {"payload": built.payload, "clientId": built.client_id, "domain": built.domain, "endpoint": built.endpoint}


@router.post("/clients", status_code=status.HTTP_201_CREATED)
async def create(
    req: CreateRequest,
    principal: Principal = Depends(require_access),
    kc: KeycloakAdmin = Depends(get_keycloak),
) -> dict[str, Any]:
    built = build_client(principal, req)
    id = await kc.create_client(built.payload)
    _audit(principal, "created", built.payload, preset=req.preset)
    # Confidential clients get a generated secret the user needs in order to
    # configure the other end.
    secret = await kc.get_client_secret(id) if has_secret(built.payload) else None
    return {"id": id, "clientId": built.client_id, "secret": secret}


# --- Existing clients ---


@router.get("/clients")
async def list_clients(
    principal: Principal = Depends(require_access), kc: KeycloakAdmin = Depends(get_keycloak)
) -> list[dict[str, Any]]:
    clients = [c for c in await kc.list_clients() if can_access(principal, c)]
    return sorted((_summary(c) for c in clients), key=lambda c: c["clientId"])


@router.get("/clients/{id}")
async def get_client(
    id: ClientId, principal: Principal = Depends(require_access), kc: KeycloakAdmin = Depends(get_keycloak)
) -> dict[str, Any]:
    return _detail(await _load(kc, id, principal))


class UpdateRequest(BaseModel):
    """The fields a user may change. Anything else in the body is ignored."""

    name: str | None = None
    enabled: bool | None = None
    redirectUris: list[str] | None = None
    postLogoutRedirectUris: list[str] | None = None
    webOrigins: list[str] | None = None
    # Admins only. An empty string removes the owner, making the client admin-only.
    owner: str | None = None


def _clean(uris: list[str] | None) -> list[str] | None:
    return None if uris is None else [u.strip() for u in uris if u.strip()]


@router.put("/clients/{id}")
async def update_client(
    id: ClientId,
    req: UpdateRequest,
    principal: Principal = Depends(require_access),
    kc: KeycloakAdmin = Depends(get_keycloak),
) -> dict[str, Any]:
    client = await _load_mutable(kc, id, principal)
    saml = client.get("protocol") == "saml"

    redirect_uris = _clean(req.redirectUris)
    post_logout = None if saml else _clean(req.postLogoutRedirectUris)
    web_origins = _clean(req.webOrigins)
    check_uris(
        principal,
        redirect_uris=redirect_uris or [],
        post_logout_uris=post_logout or [],
        web_origins=web_origins or [],
    )

    update: dict[str, Any] = {}
    attributes: dict[str, str] = {}
    if req.name is not None:
        update["name"] = req.name.strip()
    if req.enabled is not None:
        update["enabled"] = req.enabled
    if redirect_uris is not None:
        update["redirectUris"] = redirect_uris
    if web_origins is not None:
        update["webOrigins"] = web_origins
    if post_logout is not None:
        # Keycloak merges attributes on update, and an empty value removes the
        # key, so sending only the ones we change leaves the others untouched.
        attributes[POST_LOGOUT_ATTRIBUTE] = "##".join(post_logout)
    if "owner" in req.model_fields_set:
        if not principal.is_admin:
            raise HTTPException(status.HTTP_403_FORBIDDEN, "Bara administratörer kan byta ägande kår.")
        new_owner = (req.owner or "").strip()
        if new_owner:
            check_owner(principal, new_owner)
        attributes[OWNER_ATTRIBUTE] = new_owner
    if attributes:
        update["attributes"] = attributes

    if update:
        await kc.update_client(id, update)
        _audit(principal, "updated", client, fields=",".join(sorted(update)), new_owner=attributes.get(OWNER_ATTRIBUTE))
    return _detail(await kc.get_client(id) or client)


@router.delete("/clients/{id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_client(
    id: ClientId, principal: Principal = Depends(require_access), kc: KeycloakAdmin = Depends(get_keycloak)
) -> None:
    client = await _load_mutable(kc, id, principal)
    await kc.delete_client(id)
    _audit(principal, "deleted", client)


@router.get("/clients/{id}/secret")
async def get_secret(
    id: ClientId, principal: Principal = Depends(require_access), kc: KeycloakAdmin = Depends(get_keycloak)
) -> dict[str, str]:
    client = await _load(kc, id, principal)
    if not has_secret(client):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Klienten har ingen client secret.")
    _audit(principal, "viewed secret of", client)
    return {"value": await kc.get_client_secret(id)}


async def _membership_scope_id(kc: KeycloakAdmin, client: dict[str, Any]) -> str:
    if client.get("protocol") == "saml":
        raise HTTPException(status.HTTP_400_BAD_REQUEST, f"{MEMBERSHIP_SCOPE} gäller bara OIDC-klienter.")
    scope = await kc.find_client_scope(MEMBERSHIP_SCOPE)
    if scope is None:
        raise HTTPException(status.HTTP_409_CONFLICT, f"Scopet {MEMBERSHIP_SCOPE} finns inte i den här realmen.")
    return scope["id"]


@router.put("/clients/{id}/memberships-scope", status_code=status.HTTP_204_NO_CONTENT)
async def add_memberships(
    id: ClientId, principal: Principal = Depends(require_access), kc: KeycloakAdmin = Depends(get_keycloak)
) -> None:
    client = await _load_mutable(kc, id, principal)
    await kc.add_default_client_scope(id, await _membership_scope_id(kc, client))
    _audit(principal, "added memberships scope to", client)


@router.delete("/clients/{id}/memberships-scope", status_code=status.HTTP_204_NO_CONTENT)
async def remove_memberships(
    id: ClientId, principal: Principal = Depends(require_access), kc: KeycloakAdmin = Depends(get_keycloak)
) -> None:
    client = await _load_mutable(kc, id, principal)
    await kc.remove_default_client_scope(id, await _membership_scope_id(kc, client))
    _audit(principal, "removed memberships scope from", client)
