"""
OpenCage geocoding provider.

Docs: https://opencagedata.com/api

Never fabricates coordinates: if OpenCage is not configured or the
request fails, ProviderUnavailableError is raised rather than
returning a guessed location (Master Blueprint §78, §108).
"""
from __future__ import annotations

import httpx

from app.core.config import settings
from app.core.redaction import redact_secrets
from app.core.resilience import CircuitOpenError
from app.providers.http_resilience import resilient_request, safe_error_text
from app.core.exceptions import NotFoundError, ProviderUnavailableError
from app.core.logging import get_logger
from app.providers.geocoding.interface import GeocodeResult, GeocodingProvider

logger = get_logger(__name__)

_BASE_URL = "https://api.opencagedata.com/geocode/v1/json"

# Place types worth offering in a travel location picker. Roads, buildings and house numbers are noise here.
_PLACE_TYPES = {
    "country", "state", "region", "state_district", "county", "city", "town", "village", "municipality",
    "island", "archipelago", "suburb", "neighbourhood", "city_district", "district", "locality", "borough",
    "hamlet", "peninsula", "continent",
}


def parse_suggestions(results: list[dict], limit: int) -> list[GeocodeResult]:
    """Turn raw OpenCage results into distinct, place-level GeocodeResults (pure function so it is unit-testable)."""
    picked: list[GeocodeResult] = []
    seen: set[str] = set()
    for item in results:
        components = item.get("components", {})
        kind = components.get("_type")
        geometry = item.get("geometry", {})
        formatted = item.get("formatted", "")
        if kind not in _PLACE_TYPES or not formatted or geometry.get("lat") is None or geometry.get("lng") is None:
            continue
        key = formatted.strip().lower()
        if key in seen:
            continue
        seen.add(key)
        picked.append(GeocodeResult(
            formatted_address=formatted,
            latitude=geometry["lat"],
            longitude=geometry["lng"],
            country=(components.get("country_code") or "").upper() or None,
            city=components.get("city") or components.get("town") or components.get("village") or components.get("municipality"),
            region=components.get("state"),
            provider="opencage",
            confidence=item.get("confidence"),
        ))
        if len(picked) >= limit:
            break
    return picked


class OpenCageProvider(GeocodingProvider):
    async def forward_geocode(self, query: str) -> GeocodeResult:
        return await self._call({"q": query, "limit": 1, "no_annotations": 1})

    async def reverse_geocode(self, latitude: float, longitude: float) -> GeocodeResult:
        return await self._call({"q": f"{latitude}+{longitude}", "limit": 1, "no_annotations": 1})

    async def search_places(self, query: str, limit: int = 6) -> list[GeocodeResult]:
        # Ask for more than we need: roads/buildings are filtered out afterwards.
        data = await self._fetch({"q": query, "limit": min(10, max(limit * 2, 6)), "no_annotations": 1})
        return parse_suggestions(data.get("results") or [], limit)

    async def _call(self, params: dict) -> GeocodeResult:
        data = await self._fetch(params)
        return self._first(data)

    async def _fetch(self, params: dict) -> dict:
        if not settings.OPENCAGE_API_KEY:
            raise ProviderUnavailableError("Geocoding provider is not configured.")

        params = {**params, "key": settings.OPENCAGE_API_KEY}

        async def attempt() -> dict:
            async with httpx.AsyncClient(timeout=8.0) as client:
                resp = await client.get(_BASE_URL, params=params)
                resp.raise_for_status()
                return resp.json()

        try:
            data = await resilient_request("opencage", attempt)
        except httpx.HTTPStatusError as exc:
            logger.error("opencage_http_error", status=exc.response.status_code, body=safe_error_text(exc))
            raise ProviderUnavailableError("Geocoding lookup failed. Please try again.") from exc
        except CircuitOpenError:
            raise
        except Exception as exc:  # noqa: BLE001
            logger.error("opencage_error", error=redact_secrets(exc))
            raise ProviderUnavailableError("Geocoding lookup failed. Please try again.") from exc
        return data

    @staticmethod
    def _first(data: dict) -> GeocodeResult:
        results = data.get("results") or []
        if not results:
            raise NotFoundError("No matching location found.")

        top = results[0]
        components = top.get("components", {})
        geometry = top.get("geometry", {})

        return GeocodeResult(
            formatted_address=top.get("formatted", ""),
            latitude=geometry.get("lat"),
            longitude=geometry.get("lng"),
            country=components.get("country_code", "").upper() or None,
            city=components.get("city") or components.get("town") or components.get("village"),
            region=components.get("state"),
            provider="opencage",
            confidence=top.get("confidence"),
        )
