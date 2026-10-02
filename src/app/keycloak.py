"""The Keycloak Admin REST API, called as this app's service account.

Every call is made with a client_credentials token for KC_CLIENT_ID, whose
service account holds the realm-management client roles. Who may trigger which
call is decided in routes_api.py; this module only talks to Keycloak.
"""

import asyncio
import logging
import time
from typing import Any

import httpx

from .config import Settings

logger = logging.getLogger(__name__)

# Refresh the service-account token this long before it actually expires, so a
# request never sets off with a token that dies in flight.
TOKEN_EXPIRY_MARGIN_SECONDS = 30


class KeycloakError(Exception):
    """Keycloak refused or failed a call. `status` is Keycloak's HTTP status."""

    def __init__(self, status: int, message: str) -> None:
        super().__init__(message)
        self.status = status
        self.message = message


class KeycloakAdmin:
    def __init__(self, settings: Settings, http: httpx.AsyncClient) -> None:
        self._settings = settings
        self._http = http
        self._base = settings.admin_api_url
        self._token: str | None = None
        self._token_expires_at = 0.0
        self._token_lock = asyncio.Lock()

    async def _send(self, method: str, url: str, **kwargs: Any) -> httpx.Response:
        try:
            return await self._http.request(method, url, **kwargs)
        except httpx.HTTPError as e:
            logger.error("Keycloak unreachable: %s %s: %r", method, url, e)
            raise KeycloakError(502, "Kunde inte nå Keycloak.") from e

    # --- Service-account token ---

    async def _access_token(self, *, force: bool = False) -> str:
        async with self._token_lock:
            if not force and self._token and time.monotonic() < self._token_expires_at:
                return self._token
            response = await self._send(
                "POST",
                f"{self._settings.internal_realm_url}/protocol/openid-connect/token",
                data={"grant_type": "client_credentials"},
                auth=(self._settings.KC_CLIENT_ID, self._settings.KC_CLIENT_SECRET),
            )
            if response.status_code >= 400:
                logger.error("Service-account token request failed: %s %s", response.status_code, response.text)
                raise KeycloakError(502, "Kunde inte autentisera mot Keycloak.")
            body = response.json()
            self._token = body["access_token"]
            self._token_expires_at = time.monotonic() + int(body.get("expires_in", 60)) - TOKEN_EXPIRY_MARGIN_SECONDS
            return self._token

    async def _request(self, method: str, path: str, **kwargs: Any) -> httpx.Response:
        url = f"{self._base}{path}"
        token = await self._access_token()
        response = await self._send(method, url, headers={"Authorization": f"Bearer {token}"}, **kwargs)
        if response.status_code == 401:
            # The token can be revoked or the signing key rotated under us; one
            # fresh token settles which.
            token = await self._access_token(force=True)
            response = await self._send(method, url, headers={"Authorization": f"Bearer {token}"}, **kwargs)
        if response.status_code >= 400:
            raise KeycloakError(response.status_code, _error_detail(response))
        return response

    # --- Clients ---

    async def list_clients(self) -> list[dict[str, Any]]:
        return (await self._request("GET", "/clients")).json()

    async def get_client(self, id: str) -> dict[str, Any] | None:
        try:
            return (await self._request("GET", f"/clients/{id}")).json()
        except KeycloakError as e:
            if e.status == 404:
                return None
            raise

    async def create_client(self, representation: dict[str, Any]) -> str:
        """Create a client and return its Keycloak id (not its clientId)."""
        response = await self._request("POST", "/clients", json=representation)
        # 201 with an empty body; the new id is the last segment of Location.
        location = response.headers.get("Location", "")
        return location.rstrip("/").rsplit("/", 1)[-1]

    async def update_client(self, id: str, representation: dict[str, Any]) -> None:
        # A partial representation is fine: Keycloak only applies the fields that
        # are present, and merges attributes key by key.
        await self._request("PUT", f"/clients/{id}", json=representation)

    async def delete_client(self, id: str) -> None:
        await self._request("DELETE", f"/clients/{id}")

    async def get_client_secret(self, id: str) -> str:
        return (await self._request("GET", f"/clients/{id}/client-secret")).json()["value"]

    # --- Client scopes ---

    async def find_client_scope(self, name: str) -> dict[str, Any] | None:
        scopes = (await self._request("GET", "/client-scopes")).json()
        return next((s for s in scopes if s.get("name") == name), None)

    async def get_default_client_scopes(self, id: str) -> list[dict[str, Any]]:
        return (await self._request("GET", f"/clients/{id}/default-client-scopes")).json()

    async def add_default_client_scope(self, id: str, scope_id: str) -> None:
        await self._request("PUT", f"/clients/{id}/default-client-scopes/{scope_id}")

    async def remove_default_client_scope(self, id: str, scope_id: str) -> None:
        await self._request("DELETE", f"/clients/{id}/default-client-scopes/{scope_id}")


def _error_detail(response: httpx.Response) -> str:
    """Keycloak reports failures as {errorMessage|error}; fall back to the status."""
    try:
        body = response.json()
    except ValueError:
        return response.reason_phrase or str(response.status_code)
    if isinstance(body, dict):
        return str(body.get("errorMessage") or body.get("error") or response.reason_phrase)
    return response.reason_phrase or str(response.status_code)
