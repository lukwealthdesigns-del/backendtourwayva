"""
IPinfoProvider — approximate country from an IP address, via ipinfo.io.

Cached in Redis (IP -> country, `IPINFO_CACHE_TTL_SECONDS`, default 24h): the same IP shows up
repeatedly (retried signups, and every login if suspicious-login analysis is enabled), and
country-from-IP is stable enough over a day that re-querying the provider adds nothing but
latency and cost. One attempt only (`idempotent=False` — see http_resilience): this is best-effort
enrichment, never worth extra latency on a request that is not about it.

Never claims exact GPS-level location (Blueprint §42 — IP Privacy): only the two-letter country.
"""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from typing import Optional

import httpx

from app.core.config import settings
from app.core.logging import get_logger
from app.core.redaction import redact_secrets
from app.providers.http_resilience import resilient_request
from app.services.cache_service import CacheService
from app.utils.net import is_public_ip

logger = get_logger(__name__)

_CACHE_PREFIX = "ipinfo:country:"
_LOCATION_CACHE_PREFIX = "ipinfo:location:"


@dataclass(frozen=True)
class IPLocation:
    """City-level, APPROXIMATE location of an IP address (never GPS-accurate)."""

    country: Optional[str] = None      # ISO 3166-1 alpha-2, upper-case
    region: Optional[str] = None
    city: Optional[str] = None
    latitude: Optional[float] = None
    longitude: Optional[float] = None
    timezone: Optional[str] = None


def _parse_location(data: dict) -> IPLocation:
    latitude = longitude = None
    loc = data.get("loc")
    if isinstance(loc, str) and "," in loc:
        try:
            lat, lon = (float(part) for part in loc.split(",", 1))
            if -90 <= lat <= 90 and -180 <= lon <= 180:
                latitude, longitude = lat, lon
        except ValueError:
            pass
    return IPLocation(
        country=(data.get("country") or "").upper() or None,
        region=data.get("region") or None,
        city=data.get("city") or None,
        latitude=latitude,
        longitude=longitude,
        timezone=data.get("timezone") or None,
    )


class IPinfoProvider:
    async def lookup_country(self, client_ip: Optional[str]) -> Optional[str]:
        if not settings.IPINFO_TOKEN or not client_ip:
            return None

        cache_key = _CACHE_PREFIX + client_ip
        cached = await CacheService.get_raw(cache_key)
        if cached is not None:
            return cached or None       # "" was cached for "looked up, no country found"

        async def attempt() -> dict:
            async with httpx.AsyncClient(timeout=3.0) as client:
                resp = await client.get(f"https://ipinfo.io/{client_ip}/json", params={"token": settings.IPINFO_TOKEN})
                resp.raise_for_status()
                return resp.json()

        try:
            data = await resilient_request("ipinfo", attempt, idempotent=False)
            country = (data.get("country") or "").upper() or None
        except Exception as exc:  # noqa: BLE001 - includes CircuitOpenError: degrade to no signal, same as any failure
            logger.warning("ipinfo_lookup_failed", error=redact_secrets(exc))
            return None

        await CacheService.set_raw(cache_key, country or "", settings.IPINFO_CACHE_TTL_SECONDS)
        return country

    async def lookup_location(self, client_ip: Optional[str]) -> Optional[IPLocation]:
        """City-level approximate location (country, region, city, coordinates, timezone) for the
        dashboard weather widget. Same rules as `lookup_country`: best-effort, one attempt,
        Redis-cached (also negative results), never raises. Non-public addresses (127.0.0.1 in
        development, LAN ranges) are skipped without calling the provider. What coordinates come
        back depends on your IPinfo plan; a country-only plan yields an IPLocation without them."""
        if not settings.IPINFO_TOKEN or not is_public_ip(client_ip):
            return None

        cache_key = _LOCATION_CACHE_PREFIX + str(client_ip)
        cached = await CacheService.get_raw(cache_key)
        if cached is not None:
            if not cached:                       # "" = looked up, nothing usable
                return None
            try:
                return IPLocation(**json.loads(cached))
            except (ValueError, TypeError):
                pass                             # corrupt entry: fall through and re-query

        async def attempt() -> dict:
            async with httpx.AsyncClient(timeout=3.0) as client:
                resp = await client.get(f"https://ipinfo.io/{client_ip}/json", params={"token": settings.IPINFO_TOKEN})
                resp.raise_for_status()
                return resp.json()

        try:
            data = await resilient_request("ipinfo", attempt, idempotent=False)
        except Exception as exc:  # noqa: BLE001 - includes CircuitOpenError: degrade to no signal
            logger.warning("ipinfo_location_lookup_failed", error=redact_secrets(exc))
            return None

        location = None if data.get("bogon") else _parse_location(data)
        if location is not None and not (location.country or location.latitude is not None):
            location = None
        await CacheService.set_raw(
            cache_key, json.dumps(asdict(location)) if location else "", settings.IPINFO_CACHE_TTL_SECONDS
        )
        return location
