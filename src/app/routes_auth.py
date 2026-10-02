"""/auth: logging in and out."""

import html
import logging
import secrets

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from .authz import principal_from_claims
from .config import get_settings
from .oidc import OidcClient, OIDCError, generate_pkce_pair
from .session import SESSION_COOKIE, SessionStore, delete_session_cookie, get_store, set_session_cookie

logger = logging.getLogger(__name__)

settings = get_settings()

router = APIRouter()


def get_oidc(request: Request) -> OidcClient:
    return request.app.state.oidc


def _login_failed(message: str) -> HTMLResponse:
    # Plain and in Swedish: this is shown to the user instead of the SPA. The
    # message can come from the query string, so it is escaped.
    return HTMLResponse(
        "<!doctype html><meta charset=utf-8><title>ScoutID Admin</title>"
        f"<p>Inloggningen misslyckades: {html.escape(message)}</p>"
        '<p><a href="/auth/login">Försök igen</a></p>',
        status_code=400,
    )


def _signed_in(store: SessionStore, claims: dict, id_token: str | None) -> RedirectResponse:
    principal = principal_from_claims(claims, settings)
    session_id = store.create(principal, id_token)
    logger.info(
        "Login: %s admin=%s groups=%s",
        principal,
        principal.is_admin,
        ",".join(sorted(principal.groups)) or "-",
    )
    response = RedirectResponse(f"{settings.PUBLIC_URL}/", status_code=302)
    set_session_cookie(response, session_id)
    return response


@router.get("/login")
async def login(store: SessionStore = Depends(get_store), oidc: OidcClient = Depends(get_oidc)) -> RedirectResponse:
    store.sweep()
    if settings.FAKE_USER_CLAIMS:
        return _signed_in(store, settings.FAKE_USER_CLAIMS, None)

    state = secrets.token_urlsafe(32)
    nonce = secrets.token_urlsafe(32)
    verifier, challenge = generate_pkce_pair()
    store.add_pending(state, verifier, nonce)
    return RedirectResponse(oidc.authorization_url(state=state, code_challenge=challenge, nonce=nonce), 302)


@router.get("/callback", response_model=None)
async def callback(
    state: str | None = None,
    code: str | None = None,
    error: str | None = None,
    error_description: str | None = None,
    store: SessionStore = Depends(get_store),
    oidc: OidcClient = Depends(get_oidc),
) -> RedirectResponse | HTMLResponse:
    # Popped before anything else, so a state can only ever be redeemed once.
    pending = store.pop_pending(state)
    if error:
        logger.info("Keycloak returned error %s: %s", error, error_description)
        return _login_failed(error_description or error)
    if pending is None or not code:
        # Typically a callback that sat in the browser history, or a login that
        # took longer than the pending-login TTL.
        return _login_failed("inloggningen hann gå ut eller har redan använts.")

    try:
        tokens = await oidc.exchange_code(code=code, code_verifier=pending.code_verifier)
        id_token = tokens.get("id_token")
        if not id_token:
            raise OIDCError("token response has no id_token")
        claims = await oidc.verify_id_token(id_token, nonce=pending.nonce)
    except OIDCError as e:
        logger.warning("Login failed: %s", e)
        return _login_failed("kunde inte verifiera inloggningen hos ScoutID.")

    return _signed_in(store, claims, id_token)


@router.get("/logout")
async def logout(
    request: Request, store: SessionStore = Depends(get_store), oidc: OidcClient = Depends(get_oidc)
) -> RedirectResponse:
    session = store.delete(request.cookies.get(SESSION_COOKIE))
    if settings.FAKE_USER_CLAIMS:
        target = f"{settings.PUBLIC_URL}/"
    else:
        target = oidc.end_session_url(session.id_token if session else None)
    response = RedirectResponse(target, status_code=302)
    delete_session_cookie(response)
    return response
