"""
Hotel and attraction discovery from OpenStreetMap through the Overpass API (keyless). The venue source when Google Places is off.

Honest by construction: OSM has no ratings, prices or availability, so a hotel is `kind="discovery"` with no price, and nothing here
fabricates a rating. Data is © OpenStreetMap contributors (ODbL): attribution is carried in `source_url`/`maps_url`.

Courtesy toward the shared public Overpass service: one query per ~1 km cell and kind with a 7-day cache, at most two queries in flight
and a cool-down after 429/5xx (all shared with the opening-hours lookups), and a circuit breaker. A reply that is "200 OK" but says the
query timed out is treated as an outage, never cached and never reported as "no venues".
"""
from __future__ import annotations

from datetime import date
from typing import Any, Optional

import httpx

from app.core.config import settings
from app.core.exceptions import ProviderUnavailableError
from app.core.logging import get_logger
from app.core.resilience import get_breaker
from app.providers.activities.interface import ActivityProvider, ActivityResult
from app.providers.hotels.interface import HotelDiscoveryProvider, HotelOffer
from app.providers.opening_hours import overpass_provider as shared
from app.providers.opening_hours.overpass_policy import cooldown_seconds
from app.providers.osm import discovery
from app.services.cache_service import CacheService

logger = get_logger(__name__)

PROVIDER = "osm"
_USER_AGENT = "TourWayva/1.0 (venue discovery)"
_CACHE_TTL_SECONDS = 7 * 24 * 3600
_BUSY = "Place search is busy right now. Please try again in a moment."


def _cache_key(kind: str, lat: float, lon: float, radius_m: int) -> str:
    return f"osmdisc:{kind}:{lat:.2f}:{lon:.2f}:{radius_m}"      # 2 decimals is about 1.1 km: nearby searches share one answer


def endpoints() -> list[str]:
    """The Overpass servers to try, in order: the configured one, then the fallbacks. Only http(s) URLs, no repeats."""
    seen: set[str] = set()
    out: list[str] = []
    for url in (settings.OVERPASS_API_URL, *str(settings.OVERPASS_FALLBACK_URLS or "").split(",")):
        url = url.strip()
        if url.startswith(("http://", "https://")) and url not in seen:
            seen.add(url)
            out.append(url)
    return out


def _worth_trying_elsewhere(status: int) -> bool:
    return status == 429 or status >= 500        # busy or broken: another server may answer; a 4xx is OUR query's fault


async def _post(query: str) -> dict[str, Any]:
    """One Overpass call under the shared courtesy rules, failing over to the next server when one cannot answer. Raises
    ProviderUnavailableError only when every server failed (and only then starts the shared cool-down)."""
    breaker = get_breaker("overpass")
    if shared.cooling_down() or not breaker.allow():
        raise ProviderUnavailableError(_BUSY)
    timeout = settings.OSM_DISCOVERY_TIMEOUT_SECONDS
    last_error: Optional[Exception] = None
    pause = 0.0
    for url in endpoints():
        host = url.split("/")[2] if url.count("/") >= 2 else url
        try:
            async with shared.shared_gate():
                async with httpx.AsyncClient(timeout=timeout + 5, headers={"User-Agent": _USER_AGENT}) as client:
                    response = await client.post(url, data={"data": query})
            if response.status_code >= 400:
                last_error = httpx.HTTPStatusError(f"overpass returned {response.status_code}", request=response.request, response=response)
                logger.warning("osm_discovery_endpoint_failed", host=host, status=response.status_code)
                if not _worth_trying_elsewhere(response.status_code):
                    break
                pause = max(pause, cooldown_seconds(response.status_code, response.headers.get("Retry-After")))
                continue
            payload = response.json()
        except (httpx.HTTPError, ValueError) as exc:
            last_error = exc
            logger.warning("osm_discovery_endpoint_failed", host=host, kind=type(exc).__name__, error=str(exc)[:200] or "no detail")
            continue
        if discovery.overpass_gave_up(payload):     # 200 OK but "query timed out": try the next server, never cache it
            last_error = ProviderUnavailableError("overpass gave up")
            logger.warning("osm_discovery_gave_up", host=host, remark=str(payload.get("remark"))[:120])
            continue
        breaker.record_success()
        return payload
    breaker.record_failure()
    shared.start_cooldown(pause)
    raise ProviderUnavailableError(_BUSY) from last_error


async def _places(kind: str, lat: float, lon: float, radius_m: int) -> list[dict[str, Any]]:
    """Ranked places near (lat, lon). The raw cell answer is cached; ranking by distance happens per request."""
    radius_m = max(300, min(int(radius_m), 20000))
    key = _cache_key(kind, lat, lon, radius_m)
    cached = await CacheService.get_json(key)
    hotel = kind == "hotels"
    if isinstance(cached, list):
        raw = cached
    else:
        timeout = settings.OSM_DISCOVERY_TIMEOUT_SECONDS
        query = (discovery.hotel_query if hotel else discovery.attraction_query)(round(lat, 2), round(lon, 2), radius_m, timeout)
        raw = discovery.parse_elements(await _post(query), hotel=hotel)
        await CacheService.set_json(key, raw, _CACHE_TTL_SECONDS)   # an empty list is a real answer for a remote spot
    return discovery.rank_and_dedupe(raw, lat, lon, settings.OSM_DISCOVERY_MAX_RESULTS)


def _osm_url(ref: str) -> str:
    return f"https://www.openstreetmap.org/{ref}"


def _to_offer(p: dict[str, Any], *, check_in: Optional[date] = None, check_out: Optional[date] = None) -> HotelOffer:
    stars = p.get("stars")
    summary = " · ".join(x for x in (p.get("label"), f"{stars}-star" if stars and str(stars).replace(".", "").isdigit() else None) if x) or None
    return HotelOffer(
        hotel_id=f"osm:{p['ref']}", hotel_name=p["name"], offer_id=None, latitude=p["latitude"], longitude=p["longitude"],
        check_in=check_in, check_out=check_out, room_description=summary, price_total=None, currency=None, provider=PROVIDER,
        kind="discovery", provider_reference=p["ref"], address=p.get("address"), rating=None, user_rating_count=None, photo_url=None,
        website=p.get("website"), maps_url=_osm_url(p["ref"]), price_level=None,
    )


class OsmHotelDiscoveryProvider(HotelDiscoveryProvider):
    async def discover_hotels(self, *, latitude: float, longitude: float, destination: str, check_in: Optional[date] = None,
                              check_out: Optional[date] = None, max_hotels: int = 20) -> list[HotelOffer]:
        places = await _places("hotels", latitude, longitude, settings.OSM_DISCOVERY_RADIUS_M)
        return [_to_offer(p, check_in=check_in, check_out=check_out) for p in places[: max(1, max_hotels)]]

    async def get_hotel(self, *, reference: str) -> Optional[HotelOffer]:
        query = discovery.lookup_query(reference, settings.OSM_DISCOVERY_TIMEOUT_SECONDS)
        if query is None:
            return None
        found = discovery.parse_elements(await _post(query), hotel=True)
        return _to_offer(found[0]) if found else None


class OsmActivityProvider(ActivityProvider):
    async def search_activities(self, *, latitude: float, longitude: float, radius_km: int = 5) -> list[ActivityResult]:
        places = await _places("attractions", latitude, longitude, int(radius_km) * 1000)
        return [
            ActivityResult(
                activity_id=f"osm:{p['ref']}", name=p["name"], description=p.get("description") or p.get("label"), latitude=p["latitude"],
                longitude=p["longitude"], price_amount=None, currency=None, picture_url=None, booking_link=None, provider=PROVIDER,
                rating=None, user_rating_count=None, address=p.get("address"), maps_url=_osm_url(p["ref"]), website=p.get("website"),
            )
            for p in places
        ]
