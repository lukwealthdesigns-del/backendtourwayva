"""OpenStreetMap opening hours via the Overpass API (keyless, free).

Best-effort by nature: OSM is community-maintained, so coverage is patchy and a
value can be stale. That is why a mismatch downstream is only ever a WARNING
that names its source, and why every failure path here returns None ("unknown")
instead of raising or guessing.
"""
from __future__ import annotations

import asyncio
import time
from typing import Any, Optional

import httpx

from app.core.config import settings
from app.core.logging import get_logger
from app.core.resilience import get_breaker
from app.providers.opening_hours.interface import OpeningHoursProvider, OpeningHoursResult
from app.providers.opening_hours.matching import best_match, normalize_name
from app.providers.opening_hours.overpass_policy import CELL_MARGIN_M, cell_cache_key, cell_of, cooldown_seconds, slim_elements
from app.services.cache_service import (
    TTL_OPENING_HOURS_MISS_SECONDS,
    TTL_OPENING_HOURS_SECONDS,
    CacheService,
    opening_hours_cache_key,
)

logger = get_logger(__name__)

SOURCE = "openstreetmap"
_USER_AGENT = "TourWayva/1.0 (itinerary opening-hours check)"   # Overpass asks clients to identify themselves
_MAX_CONCURRENT = 2                      # never more than two queries in flight against the shared public service
_semaphore: Optional[asyncio.Semaphore] = None
_cooldown_until = 0.0                    # monotonic time before which we do not call Overpass at all (after a 429 / 504)
_inflight: dict[str, "asyncio.Future[Optional[list[dict[str, Any]]]]"] = {}


def _gate() -> asyncio.Semaphore:
    global _semaphore
    if _semaphore is None:
        _semaphore = asyncio.Semaphore(_MAX_CONCURRENT)
    return _semaphore


def shared_gate() -> asyncio.Semaphore:
    """The one in-flight limit for the public Overpass service, shared with place discovery (app/providers/osm)."""
    return _gate()


def cooling_down() -> bool:
    return time.monotonic() < _cooldown_until


def start_cooldown(pause: float) -> None:
    """Leave Overpass alone for `pause` seconds (after a 429/5xx), for every caller."""
    global _cooldown_until
    if pause:
        _cooldown_until = max(_cooldown_until, time.monotonic() + pause)


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
        """Elements with an `opening_hours` tag near the point, or None when Overpass could not answer.

        One query per map cell (venues in the same neighbourhood share it), answers cached, identical simultaneous requests merged
        into one, at most two in flight, and a pause after Overpass says it is busy."""
        key = cell_cache_key(latitude, longitude)
        cached = await CacheService.get_json(key)
        if isinstance(cached, dict) and isinstance(cached.get("elements"), list):
            return cached["elements"]                                   # includes "nothing here": [] is a real answer

        if time.monotonic() < _cooldown_until:
            return None                                                 # told to slow down: unknown for now, not cached
        waiting = _inflight.get(key)
        if waiting is not None:
            return await asyncio.shield(waiting)                        # someone is already asking about this very cell

        future: "asyncio.Future[Optional[list[dict[str, Any]]]]" = asyncio.get_running_loop().create_future()
        _inflight[key] = future
        result: Optional[list[dict[str, Any]]] = None
        try:
            result = await self._fetch(latitude, longitude)
            if result is not None:
                await CacheService.set_json(key, {"elements": result}, TTL_OPENING_HOURS_SECONDS)
        finally:
            _inflight.pop(key, None)
            if not future.done():
                future.set_result(result)
        return result

    async def _fetch(self, latitude: float, longitude: float) -> Optional[list[dict[str, Any]]]:
        global _cooldown_until
        breaker = get_breaker("overpass")
        if not breaker.allow():
            return None
        lat, lon = cell_of(latitude, longitude)
        # Only numbers are interpolated (the venue name never enters the query), so there is nothing to inject.
        query = (
            f"[out:json][timeout:{int(self.timeout)}];"
            f'nwr(around:{int(self.radius_m) + CELL_MARGIN_M},{lat:.6f},{lon:.6f})["opening_hours"];out tags 60;'
        )
        try:
            async with _gate():
                async with httpx.AsyncClient(timeout=self.timeout, headers={"User-Agent": _USER_AGENT}) as client:
                    response = await client.post(self.url, data={"data": query})
            if response.status_code >= 400:
                pause = cooldown_seconds(response.status_code, response.headers.get("Retry-After"))
                if pause:
                    _cooldown_until = max(_cooldown_until, time.monotonic() + pause)
                raise httpx.HTTPStatusError(f"overpass returned {response.status_code}", request=response.request, response=response)
            elements = response.json().get("elements", [])
        except (httpx.HTTPError, ValueError) as exc:
            breaker.record_failure()
            logger.warning("overpass_lookup_failed", kind=type(exc).__name__, error=str(exc)[:200] or "no detail")
            return None
        breaker.record_success()
        return slim_elements(elements)
