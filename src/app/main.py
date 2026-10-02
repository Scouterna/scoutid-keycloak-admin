"""The app: the SPA, /auth, /api and /healthz behind one origin.

Serving the SPA from the same process as the API is what lets the browser
reach Keycloak only through /api: there is no CORS to configure and no token in
the browser.
"""

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

import httpx
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles

from .authz import PolicyError
from .config import get_settings
from .keycloak import KeycloakAdmin, KeycloakError
from .oidc import OidcClient
from .routes_api import router as api_router
from .routes_auth import router as auth_router

logger = logging.getLogger(__name__)

settings = get_settings()

HTTP_TIMEOUT = 10.0

# Everything is same-origin. 'unsafe-inline' for styles only, for React's style
# attributes; scripts are strictly the bundle's own.
CONTENT_SECURITY_POLICY = (
    "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; "
    "img-src 'self' data:; font-src 'self' data:; connect-src 'self'; "
    "object-src 'none'; base-uri 'none'; frame-ancestors 'none'; form-action 'self'"
)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    async with httpx.AsyncClient(timeout=HTTP_TIMEOUT) as http:
        app.state.keycloak = KeycloakAdmin(settings, http)
        app.state.oidc = OidcClient(settings, http)
        logger.info("Keycloak: issuer %s, Admin API %s", settings.issuer, settings.admin_api_url)
        yield


app = FastAPI(title="ScoutID Keycloak Admin", version="0.2.0", lifespan=lifespan, docs_url=None, redoc_url=None)


@app.middleware("http")
async def headers(request: Request, call_next):
    response = await call_next(request)
    path = request.url.path
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["Referrer-Policy"] = "no-referrer"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Content-Security-Policy"] = CONTENT_SECURITY_POLICY
    if path.startswith("/assets/"):
        # Vite puts a content hash in every asset name.
        response.headers["Cache-Control"] = "public, max-age=31536000, immutable"
    else:
        response.headers["Cache-Control"] = "no-store"
    return response


@app.exception_handler(PolicyError)
async def policy_error(_: Request, exc: PolicyError) -> JSONResponse:
    return JSONResponse({"detail": str(exc)}, status_code=400)


@app.exception_handler(KeycloakError)
async def keycloak_error(request: Request, exc: KeycloakError) -> JSONResponse:
    # Keycloak's own 4xx (e.g. 409 "Client ... already exists") are meaningful to
    # the user; anything else is our problem, not theirs.
    if exc.status in (400, 404, 409):
        return JSONResponse({"detail": exc.message}, status_code=exc.status)
    logger.error("Keycloak error on %s %s: %s %s", request.method, request.url.path, exc.status, exc.message)
    return JSONResponse({"detail": f"Keycloak svarade med fel: {exc.message}"}, status_code=502)


app.include_router(api_router, prefix="/api", tags=["API"])
app.include_router(auth_router, prefix="/auth", tags=["Auth"])


@app.get("/healthz", include_in_schema=False)
async def healthz() -> dict[str, str]:
    return {"status": "ok"}


# --- The SPA ---

static_dir: Path = settings.STATIC_DIR.resolve()

if (static_dir / "index.html").is_file():
    if (static_dir / "assets").is_dir():
        app.mount("/assets", StaticFiles(directory=static_dir / "assets"), name="assets")

    @app.get("/{full_path:path}", include_in_schema=False)
    async def spa(full_path: str) -> Response:
        # Unknown API paths must not fall through to index.html, or a typo in a
        # fetch() would come back as a 200 page of HTML.
        if full_path.startswith(("api/", "auth/")):
            raise HTTPException(404)
        candidate = (static_dir / full_path).resolve()
        if full_path and candidate.is_file() and candidate.is_relative_to(static_dir):
            return FileResponse(candidate)
        # Anything else is a client-side route; the SPA sorts it out.
        return FileResponse(static_dir / "index.html")

else:
    logger.warning("No built SPA in %s; serving the API only", static_dir)
