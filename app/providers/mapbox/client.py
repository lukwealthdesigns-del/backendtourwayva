"""Shared plumbing for the Mapbox adapters: token check, one resilient GET, and the 'no route' / 'unavailable' split."""
from __future__ import annotations

import httpx

from app.core.config import settings
from app.core.exceptions import ProviderUnavailableError
from app.providers.http_resilience import resilient_request

GEOCODE_BASE = "https://api.mapbox.com/search/geocode/v6"
DIRECTIONS_BASE = "https://api.mapbox.com/directions/v5/mapbox"


def is_configured() -> bool:
    return bool(settings.MAPBOX_ACCESS_TOKEN)


async def get_json(label: str, url: str, params: dict, *, timeout: float = 8.0) -> dict:
    """GET with the shared retry/circuit-breaker policy. The token is added here and scrubbed from logs by core.redaction.
    HTTPStatusError is left for the caller to classify (a 422 means something different for geocoding and for routing)."""
    if not settings.MAPBOX_ACCESS_TOKEN:
        raise ProviderUnavailableError("Mapbox is not configured.")
    query = {**params, "access_token": settings.MAPBOX_ACCESS_TOKEN}

    async def attempt() -> dict:
        async with httpx.AsyncClient(timeout=timeout) as client:
            resp = await client.get(url, params=query)
            resp.raise_for_status()
            return resp.json()

    return await resilient_request(label, attempt)
