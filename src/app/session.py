"""Sessions, kept in process memory.

The GUI has few users and runs as a single replica, so there is no need for a
shared store: the cookie carries only a random session id, and losing every
session on a pod restart just means logging in again (silently, as long as the
Keycloak SSO session is alive).

The same store holds logins in progress, keyed by their OAuth `state`, so the
PKCE verifier and nonce never leave the server either.
"""

import logging
import secrets
import time
from dataclasses import dataclass

from fastapi import Depends, HTTPException, Request, Response, status

from .authz import Principal
from .config import get_settings

logger = logging.getLogger(__name__)

settings = get_settings()

SESSION_COOKIE = "scoutid-admin_session"
COOKIE_PATH = "/"
SAME_SITE = "lax"
# A login must come back from Keycloak within this time.
PENDING_LOGIN_TTL_SECONDS = 600
# Mutating API requests must carry this header. A cross-site page cannot set it
# without a CORS preflight, which this app never grants.
CSRF_HEADER = "X-Requested-With"
CSRF_HEADER_VALUE = "scoutid-admin"


@dataclass
class Session:
    principal: Principal
    # Kept for the id_token_hint at logout.
    id_token: str | None
    expires_at: float


@dataclass
class PendingLogin:
    code_verifier: str
    nonce: str
    expires_at: float


class SessionStore:
    def __init__(self, ttl_seconds: int) -> None:
        self._ttl = ttl_seconds
        self._sessions: dict[str, Session] = {}
        self._pending: dict[str, PendingLogin] = {}

    def create(self, principal: Principal, id_token: str | None) -> str:
        session_id = secrets.token_urlsafe(32)
        self._sessions[session_id] = Session(principal, id_token, time.time() + self._ttl)
        return session_id

    def get(self, session_id: str | None) -> Session | None:
        if not session_id:
            return None
        session = self._sessions.get(session_id)
        if session is None:
            return None
        if session.expires_at <= time.time():
            del self._sessions[session_id]
            return None
        return session

    def delete(self, session_id: str | None) -> Session | None:
        return self._sessions.pop(session_id, None) if session_id else None

    def add_pending(self, state: str, code_verifier: str, nonce: str) -> None:
        self._pending[state] = PendingLogin(code_verifier, nonce, time.time() + PENDING_LOGIN_TTL_SECONDS)

    def pop_pending(self, state: str | None) -> PendingLogin | None:
        """Take a pending login out of the store. Single use: a replayed state finds nothing."""
        pending = self._pending.pop(state, None) if state else None
        if pending is None or pending.expires_at <= time.time():
            return None
        return pending

    def sweep(self) -> None:
        """Drop everything expired. Cheap enough to run at every login."""
        now = time.time()
        self._sessions = {k: v for k, v in self._sessions.items() if v.expires_at > now}
        self._pending = {k: v for k, v in self._pending.items() if v.expires_at > now}


_store = SessionStore(settings.SESSION_MAX_AGE)


def get_store() -> SessionStore:
    return _store


# --- Cookie ---


def set_session_cookie(response: Response, session_id: str) -> None:
    response.set_cookie(
        key=SESSION_COOKIE,
        value=session_id,
        max_age=settings.SESSION_MAX_AGE,
        path=COOKIE_PATH,
        httponly=True,
        secure=not settings.INSECURE_COOKIES,
        samesite=SAME_SITE,
    )


def delete_session_cookie(response: Response) -> None:
    # A browser matches a deletion on name + domain + path, so the attributes
    # must be the same as when the cookie was set.
    response.delete_cookie(
        key=SESSION_COOKIE,
        path=COOKIE_PATH,
        httponly=True,
        secure=not settings.INSECURE_COOKIES,
        samesite=SAME_SITE,
    )


# --- Dependencies ---


def require_principal(request: Request, store: SessionStore = Depends(get_store)) -> Principal:
    session = store.get(request.cookies.get(SESSION_COOKIE))
    if session is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Inte inloggad.")
    return session.principal


def require_access(principal: Principal = Depends(require_principal)) -> Principal:
    if not principal.has_access:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Du saknar behörighet att administrera klienter.")
    return principal


def check_csrf(request: Request) -> None:
    if request.method in ("GET", "HEAD", "OPTIONS"):
        return
    origin = request.headers.get("Origin")
    if request.headers.get(CSRF_HEADER) != CSRF_HEADER_VALUE or (origin and origin != settings.public_origin):
        logger.warning("Refused %s %s: CSRF check failed (Origin %s)", request.method, request.url.path, origin)
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Förfrågan saknar giltigt ursprung.")
