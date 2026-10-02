"""Application settings, read from the environment (and a local .env file).

`env_file=".env"` resolves relative to the working directory, so run the app from
`src/` — as the container CMD does.
"""

from functools import lru_cache
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    # --- Public identity of this service ---
    # External base URL of the GUI, e.g. https://clients.id.scouterna.se. Every
    # URL handed to Keycloak (redirect_uri, post_logout_redirect_uri) is built
    # from it, never from the incoming request.
    PUBLIC_URL: str

    # --- Keycloak ---
    # Base URL the *browser* uses: the authorize and end-session redirects go
    # here, and it is the base of the issuer that id_tokens must carry.
    KC_PUBLIC_URL: str
    # Base URL the *backend* uses for the token endpoint, the JWKS and the Admin
    # REST API. In-cluster this is the Keycloak Service, so the admin host can
    # stay behind its IP allowlist. Empty means "same as KC_PUBLIC_URL", which
    # only works when the Admin API is reachable there.
    KC_INTERNAL_URL: str = ""
    KC_REALM: str = "scoutid"
    # One confidential client does both jobs: users log in through it, and its
    # service account (with realm-management roles) calls the Admin API.
    KC_CLIENT_ID: str = "scoutid-admin-gui"
    KC_CLIENT_SECRET: str

    # --- Permissions from the Scoutnet `memberships` claim ---
    # A role with this id in memberships.organisations[ADMIN_ORG_ID] makes the
    # user an admin over every client.
    ADMIN_ORG_ID: str = "692"
    ADMIN_ROLE_ID: int = 235
    # A role with this id in memberships.groups[N] (it_manager) lets the user
    # manage the clients owned by kår N.
    GROUP_MANAGER_ROLE_ID: int = 136
    # Optional: a client role on KC_CLIENT_ID that also makes a user admin, for
    # people whose Scoutnet roles do not say so. Empty disables it.
    ADMIN_CLIENT_ROLE: str = ""

    # --- Sessions ---
    # Sessions live in process memory; a restart only means logging in again.
    SESSION_MAX_AGE: int = 8 * 3600

    # --- Dev-only: bypass the identity provider ---
    # A JSON object of id_token claims. When set, /auth/login signs in as this
    # user without contacting Keycloak. The Admin API is still called for real.
    FAKE_USER_CLAIMS: dict[str, Any] = {}

    # --- Serving ---
    # The built SPA (pnpm build). Missing in API-only development, in which case
    # only /api, /auth and /healthz are served.
    STATIC_DIR: Path = Path(__file__).resolve().parents[2] / "dist"
    PORT: int = 8080
    DEBUG: bool = False
    # Drop the Secure attribute so cookies work over plain HTTP locally.
    INSECURE_COOKIES: bool = False

    model_config = SettingsConfigDict(env_file=".env")

    @field_validator("PUBLIC_URL", "KC_PUBLIC_URL", "KC_INTERNAL_URL")
    @classmethod
    def _strip_trailing_slash(cls, value: str) -> str:
        return value.rstrip("/")

    @property
    def public_origin(self) -> str:
        """scheme://host[:port] of PUBLIC_URL, as browsers send it in Origin."""
        parts = urlsplit(self.PUBLIC_URL)
        return f"{parts.scheme}://{parts.netloc}"

    @property
    def issuer(self) -> str:
        return f"{self.KC_PUBLIC_URL}/realms/{self.KC_REALM}"

    @property
    def internal_realm_url(self) -> str:
        return f"{self.KC_INTERNAL_URL or self.KC_PUBLIC_URL}/realms/{self.KC_REALM}"

    @property
    def admin_api_url(self) -> str:
        return f"{self.KC_INTERNAL_URL or self.KC_PUBLIC_URL}/admin/realms/{self.KC_REALM}"


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
