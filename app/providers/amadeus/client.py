"""
Shared Amadeus API client — used by the Hotel, Flight, and Activity
providers (Master Blueprint §20, §23-24: "Initial provider: Amadeus"
for all three).

Handles:
  - OAuth2 client-credentials token acquisition (Amadeus tokens
    expire in ~30 minutes; this caches the token in Redis so every
    hotel/flight/activity search doesn't re-authenticate)
  - Base request plumbing shared by all three resource types
  - Uniform error translation to ProviderUnavailableError/NotFoundError

Never fabricates data: any failure surfaces as a controlled
AppError subclass (Blueprint §78, §108) rather than a fake response.
"""
from __future__ import annotations

from typing import Any, Optional

import httpx

from app.core.config import settings
from app.core.exceptions import NotFoundError, ProviderUnavailableError
from app.core.logging import get_logger
from app.core.redaction import redact_secrets
from app.core.resilience import CircuitOpenError
from app.providers.http_resilience import resilient_request, safe_error_text
from app.services.cache_service import CacheService


class _AmadeusNotFound(Exception):
    """Internal signal only (404 is not transient, but also not a provider failure to log)."""


logger = get_logger(__name__)

_TOKEN_CACHE_KEY = "amadeus:access_token"
_TOKEN_URL_BY_ENV = {
    "test": "https://test.api.amadeus.com/v1/security/oauth2/token",
    "production": "https://api.amadeus.com/v1/security/oauth2/token",
}
_BASE_URL_BY_ENV = {
    "test": "https://test.api.amadeus.com",
    "production": "https://api.amadeus.com",
}


class AmadeusClient:
    def __init__(self) -> None:
        self._base_url = _BASE_URL_BY_ENV.get(settings.AMADEUS_ENV, _BASE_URL_BY_ENV["test"])
        self._token_url = _TOKEN_URL_BY_ENV.get(settings.AMADEUS_ENV, _TOKEN_URL_BY_ENV["test"])

    async def _get_access_token(self) -> str:
        cached = await CacheService.get_json(_TOKEN_CACHE_KEY)
        if cached and cached.get("access_token"):
            return cached["access_token"]

        if not (settings.AMADEUS_CLIENT_ID and settings.AMADEUS_CLIENT_SECRET):
            raise ProviderUnavailableError("Travel data provider is not configured.")

        async def attempt() -> dict:
            async with httpx.AsyncClient(timeout=10.0) as client:
                resp = await client.post(
                    self._token_url,
                    data={
                        "grant_type": "client_credentials",
                        "client_id": settings.AMADEUS_CLIENT_ID,
                        "client_secret": settings.AMADEUS_CLIENT_SECRET,
                    },
                    headers={"Content-Type": "application/x-www-form-urlencoded"},
                )
                resp.raise_for_status()
                return resp.json()

        try:
            data = await resilient_request("amadeus_auth", attempt)
        except Exception as exc:  # noqa: BLE001
            logger.error("amadeus_auth_failed", error=redact_secrets(exc))
            raise ProviderUnavailableError("Travel data provider authentication failed.") from exc

        token = data["access_token"]
        expires_in = int(data.get("expires_in", 1799))
        # Cache for slightly less than the real expiry so we never use a
        # token that's about to be rejected mid-request.
        await CacheService.set_json(_TOKEN_CACHE_KEY, {"access_token": token}, max(expires_in - 60, 60))
        return token

    async def get(self, path: str, params: Optional[dict[str, Any]] = None, *, resource: str = "generic") -> dict:
        """`resource` (e.g. "hotels", "flights", "activities") gets its OWN circuit breaker:
        Amadeus's flight search being down should not stop hotel search from being tried."""
        return await self._request("GET", path, params=params, resource=resource)

    async def post_json(self, path: str, body: dict[str, Any], *, resource: str = "generic") -> dict:
        """POST with a JSON body — used for endpoints Amadeus models as an
        action rather than a lookup, e.g. Flight Offers Price
        (re-confirming a specific offer is still valid/priced correctly
        requires POSTing the whole offer object back, not a GET by id)."""
        return await self._request("POST", path, json_body=body, resource=resource)

    async def _request(
        self,
        method: str,
        path: str,
        *,
        params: Optional[dict[str, Any]] = None,
        json_body: Optional[dict[str, Any]] = None,
        resource: str = "generic",
    ) -> dict:
        token = await self._get_access_token()
        url = f"{self._base_url}{path}"
        headers = {"Authorization": f"Bearer {token}"}
        if json_body is not None:
            headers["Content-Type"] = "application/vnd.amadeus+json"

        async def attempt() -> dict:
            async with httpx.AsyncClient(timeout=15.0) as client:
                resp = await client.request(method, url, params=params, json=json_body, headers=headers)
            if resp.status_code == 404:
                raise _AmadeusNotFound()
            resp.raise_for_status()
            return resp.json()

        try:
            return await resilient_request(f"amadeus_{resource}", attempt)
        except _AmadeusNotFound as exc:
            raise NotFoundError("No results found.") from exc
        except CircuitOpenError:
            raise
        except httpx.HTTPStatusError as exc:
            logger.error("amadeus_http_error", status=exc.response.status_code, path=path, body=safe_error_text(exc))
            raise ProviderUnavailableError("Travel data lookup failed. Please try again.") from exc
        except Exception as exc:  # noqa: BLE001
            logger.error("amadeus_error", error=redact_secrets(exc), path=path)
            raise ProviderUnavailableError("Travel data lookup failed. Please try again.") from exc


amadeus_client = AmadeusClient()
