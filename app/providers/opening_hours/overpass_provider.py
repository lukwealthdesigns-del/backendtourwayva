"""OpenStreetMap opening hours via the Overpass API (keyless, free).

Best-effort by nature: OSM is community-maintained, so coverage is patchy and a
value can be stale. That is why a mismatch downstream is only ever a WARNING
that names its source, and why every failure path here returns None ("unknown")
instead of raising or guessing.
"""
from __future__ import annotations

from typing import Any, Optional

import httpx

from app.core.config import settings
from app.core.logging import get_logger
from app.core.resilience import get_breaker
from app.providers.opening_hours.interface import OpeningHoursProvider, OpeningHoursResult
from app.providers.opening_hours.matching import best_match, normalize_name
from app.services.cache_service import (
    TTL_OPENING_HOURS_MISS_SECONDS,
    TTL_OPENING_HOURS_SECONDS,
    CacheService,
    opening_hours_cache_key,
)

logger = get_logger(__name__)

SOURCE = "openstreetmap"
_USER_AGENT = "TourWayva/1.0 (itinerary opening-hours check)"   # Overpass asks clients to identify themselves


class OverpassOpeningHoursProvider(OpeningHoursProvider):
    def __init__(
        self, *, url: Optional[str] = None, timeout: Optional[int] = None, radius_m: Optional[int] = None
    ):
        self.url = url or settings.OVERPASS_API_URL
        self.timeout = timeout or settings.OPENING_HOURS_TIMEOUT_SECONDS
        self.radius_m = radius_m or settings.OPENING_HOURS_SEARCH_RADIUS_M

    async def lookup(self, *, name: str, latitude: float, longitude: float) -> Optional[OpeningHoursResult]:
        normalized = normalize_name(name)
        if not normalized:
            return None
        key = opening_hours_cache_key(latitude, longitude, normalized)

        cached = await CacheService.get_json(key)
        if isinstance(cached, dict):
            if cached.get("none"):
                return None
            if isinstance(cached.get("raw"), str):
                return OpeningHoursResult(raw=cached["raw"], source=SOURCE, matched_name=cached.get("matched_name"))

        elements = await self._query(latitude, longitude)
        if elements is None:
            return None            # transport problem: unknown, and deliberately NOT cached as "no data"

        match = best_match(name, elements)
        if match is None:
            await CacheService.set_json(key, {"none": True}, TTL_OPENING_HOURS_MISS_SECONDS)
            return None
        tags = match["tags"]
        result = OpeningHoursResult(raw=tags["opening_hours"].strip(), source=SOURCE, matched_name=tags.get("name"))
        await CacheService.set_json(
            key, {"raw": result.raw, "matched_name": result.matched_name}, TTL_OPENING_HOURS_SECONDS
        )
        return result

    async def _query(self, latitude: float, longitude: float) -> Optional[list[dict[str, Any]]]:
        """Elements with an `opening_hours` tag near the point, or None when Overpass could not answer."""
        breaker = get_breaker("overpass")
        if not breaker.allow():
            return None
        # Only numbers are interpolated (the venue name never enters the query), so there is nothing to inject.
        query = (
            f"[out:json][timeout:{int(self.timeout)}];"
            f'nwr(around:{int(self.radius_m)},{latitude:.6f},{longitude:.6f})["opening_hours"];out tags 25;'
        )
        try:
            async with httpx.AsyncClient(timeout=self.timeout, headers={"User-Agent": _USER_AGENT}) as client:
                response = await client.post(self.url, data={"data": query})
            if response.status_code >= 400:
                raise httpx.HTTPStatusError("overpass error", request=response.request, response=response)
            elements = response.json().get("elements", [])
        except (httpx.HTTPError, ValueError) as exc:
            breaker.record_failure()
            logger.warning("overpass_lookup_failed", error=str(exc)[:200])
            return None
        breaker.record_success()
        return [e for e in elements if isinstance(e, dict)]
