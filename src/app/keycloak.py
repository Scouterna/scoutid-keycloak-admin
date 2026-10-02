"""The Keycloak Admin REST API, called with the user's own access token.

The token arrives from the SPA and is forwarded as-is, so Keycloak's own
realm-management roles apply on top of the Scoutnet rules in routes_api.py:
a call this app allows still fails if the user lacks manage-clients. This
module only talks to Keycloak.
"""

import logging
from typing import Any

import httpx

from .config import Settings

logger = logging.getLogger(__name__)


class KeycloakError(Exception):
    """Keycloak refused or failed a call. `status` is Keycloak's HTTP status."""

    def __init__(self, status: int, message: str) -> None:
        super().__init__(message)
        self.status = status
        self.message = message


class KeycloakAdmin:
    """Admin API calls on behalf of one user, made with that user's token."""

    def __init__(self, settings: Settings, http: httpx.AsyncClient, token: str) -> None:
        self._http = http
        self._base = settings.admin_api_url
        self._headers = {"Authorization": f"Bearer {token}"}

    async def _request(self, method: str, path: str, **kwargs: Any) -> httpx.Response:
        url = f"{self._base}{path}"
        try:
            response = await self._http.request(method, url, headers=self._headers, **kwargs)
        except httpx.HTTPError as e:
            logger.error("Keycloak unreachable: %s %s: %r", method, url, e)
            raise KeycloakError(502, "Kunde inte nå Keycloak.") from e
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
