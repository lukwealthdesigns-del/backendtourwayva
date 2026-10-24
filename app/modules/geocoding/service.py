"""
GeocodingService: ordered provider chains + "reuse before regenerate" caching, both governed by the location data-use policy.

Three kinds of lookup, each with its own provider order in settings:
  * suggest (GEOCODING_SUGGEST_PROVIDERS): autocomplete while typing. Mapbox first. Mapbox results are never cached or stored.
  * forward / reverse (GEOCODING_PROVIDERS): a place or destination whose answer callers keep. Mapbox is only in this chain
    when MAPBOX_GEOCODING_PERMANENT is true (Mapbox forbids keeping temporary results); otherwise OpenCage answers.
  * venue (GEOCODING_VENUE_PROVIDERS, forward_geocode(precise=True)): a named restaurant, hotel or attraction. OpenCage (OpenStreetMap
    points of interest) first; Mapbox, which has no POIs, is only accepted for confident street addresses.
A provider that FAILS hands over to the next one; a provider that merely finds nothing does too, except for short autocomplete
queries (an empty list is a fine answer to "ab"). What gets cached and where is decided per provider by app/core/location_policy.
"""
from __future__ import annotations

from typing import Awaitable, Callable, Optional

from app.core import location_policy as policy
from app.core.config import settings
from app.core.exceptions import NotFoundError, ProviderUnavailableError
from app.core.logging import get_logger
from app.modules.geocoding.schemas import GeocodeResponse, LocationSuggestion, LocationSuggestResponse
from app.providers.geocoding.interface import GeocodeResult, GeocodingProvider
from app.providers.geocoding.opencage_provider import OpenCageProvider
from app.providers.mapbox.geocoding_provider import MapboxGeocodingProvider
from app.providers.travel.resolver import parse_order
from app.services import durable_cache
from app.services.cache_service import (
    TTL_GEOCODING_SECONDS,
    CacheService,
    geocode_cache_key,
    reverse_geocode_cache_key,
)

logger = get_logger(__name__)

SUGGEST, FORWARD, VENUE = "suggest", "forward", "venue"
_ORDER_SETTING = {SUGGEST: "GEOCODING_SUGGEST_PROVIDERS", FORWARD: "GEOCODING_PROVIDERS", VENUE: "GEOCODING_VENUE_PROVIDERS"}
_opencage = OpenCageProvider()
_MIN_CHARS_TO_TRY_NEXT_ON_EMPTY = 4


def build_chain(kind: str) -> list[tuple[str, GeocodingProvider]]:
    """The providers that may answer this kind of lookup right now, in priority order (unconfigured or ineligible ones skipped)."""
    chain: list[tuple[str, GeocodingProvider]] = []
    for name in parse_order(getattr(settings, _ORDER_SETTING[kind])):
        if name == "mapbox":
            if not settings.MAPBOX_ACCESS_TOKEN:
                continue
            if kind != SUGGEST and not settings.MAPBOX_GEOCODING_PERMANENT:
                continue   # these answers are kept; Mapbox only allows keeping permanent geocoding results
            chain.append((name, MapboxGeocodingProvider(permanent=settings.MAPBOX_GEOCODING_PERMANENT, precise=(kind == VENUE))))
        elif name == "opencage" and settings.OPENCAGE_API_KEY:
            chain.append((name, _opencage))
    return chain


async def _first_answer(chain: list[tuple[str, GeocodingProvider]], call: Callable[[GeocodingProvider], Awaitable]):
    """(provider name, result) from the first provider that answers. 'Not found' and 'unavailable' both move on; if nobody
    answers, an outage is reported in preference to a plain 'not found' (we could not rule the place out)."""
    unavailable: Optional[Exception] = None
    not_found: Optional[Exception] = None
    for name, provider in chain:
        try:
            return name, await call(provider)
        except NotFoundError as exc:
            not_found = exc
        except ProviderUnavailableError as exc:
            unavailable = exc
            logger.warning("geocoding_provider_unavailable", provider=name)
    raise unavailable or not_found or ProviderUnavailableError("Geocoding provider is not configured.")


def _normalize_query(query: str) -> str:
    return " ".join(query.strip().lower().split())


def _result_to_response(result: GeocodeResult, *, source: str) -> GeocodeResponse:
    return GeocodeResponse(
        formatted_address=result.formatted_address, latitude=result.latitude, longitude=result.longitude,
        country=result.country, city=result.city, region=result.region, source=source, provider=result.provider,
    )


def suggest_cache_key(normalized_query: str, limit: int) -> str:
    return f"geosuggest:{limit}:{normalized_query}"


def _rules() -> dict:
    return {"mapbox_permanent": settings.MAPBOX_GEOCODING_PERMANENT}


class GeocodingService:
    async def suggest(self, query: str, limit: int = 6) -> LocationSuggestResponse:
        """Autocomplete for location pickers. Returns an empty list, never a 404, for no matches."""
        normalized = _normalize_query(query)
        cache_key = suggest_cache_key(normalized, limit)

        cached = await CacheService.get_json(cache_key)
        if cached is not None:
            return LocationSuggestResponse(results=cached, source="cache")

        chain = build_chain(SUGGEST)
        if not chain:
            raise ProviderUnavailableError("Geocoding provider is not configured.")
        found: list[GeocodeResult] = []
        used: Optional[str] = None
        unavailable: Optional[Exception] = None
        for name, provider in chain:
            try:
                found = await provider.search_places(query, limit)
            except ProviderUnavailableError as exc:
                unavailable = exc
                logger.warning("geocoding_provider_unavailable", provider=name)
                continue
            used = name
            if found or len(normalized) < _MIN_CHARS_TO_TRY_NEXT_ON_EMPTY:
                break
        if used is None:
            raise unavailable or ProviderUnavailableError("Geocoding lookup failed. Please try again.")

        results = [
            LocationSuggestion(formatted_address=r.formatted_address, latitude=r.latitude, longitude=r.longitude,
                               country=r.country, city=r.city, region=r.region)
            for r in found
        ]
        ttl = policy.cache_ttl(used, policy.Operation.SUGGEST, TTL_GEOCODING_SECONDS, **_rules())
        if ttl:
            await CacheService.set_json(cache_key, [x.model_dump() for x in results], ttl)
        return LocationSuggestResponse(results=results, source="live", provider=used)

    async def forward_geocode(self, query: str, *, precise: bool = False) -> GeocodeResponse:
        """precise=True is for a named venue or address (restaurant, hotel, attraction) rather than a city or country."""
        normalized = _normalize_query(query)
        cache_key = geocode_cache_key(normalized + ("|venue" if precise else ""))

        hit = await self._read(cache_key)
        if hit is not None:
            return hit

        chain = build_chain(VENUE if precise else FORWARD)
        name, result = await _first_answer(chain, lambda p: p.forward_geocode(query))
        response = _result_to_response(result, source="live")
        await self._remember(cache_key, name, policy.Operation.FORWARD_GEOCODE, "forward", response)
        return response

    async def reverse_geocode(self, latitude: float, longitude: float) -> GeocodeResponse:
        cache_key = reverse_geocode_cache_key(latitude, longitude)

        hit = await self._read(cache_key)
        if hit is not None:
            return hit

        name, result = await _first_answer(build_chain(FORWARD), lambda p: p.reverse_geocode(latitude, longitude))
        response = _result_to_response(result, source="live")
        await self._remember(cache_key, name, policy.Operation.REVERSE_GEOCODE, "reverse", response)
        return response

    @staticmethod
    async def _read(cache_key: str) -> Optional[GeocodeResponse]:
        cached = await CacheService.get_json(cache_key)
        if cached is not None:
            return GeocodeResponse(**{**cached, "source": "cache"})
        durable = await durable_cache.get_geocode(cache_key)
        if durable is not None:
            response = GeocodeResponse(**{**durable, "source": "cache"})
            await CacheService.set_json(cache_key, response.model_dump(), TTL_GEOCODING_SECONDS)
            return response
        return None

    @staticmethod
    async def _remember(cache_key: str, provider: str, operation: policy.Operation, kind: str, response: GeocodeResponse) -> None:
        """Keep the answer only as long as, and only where, the provider's terms allow (see location_policy)."""
        use = policy.data_use(provider, operation, **_rules())
        ttl = policy.cache_ttl(provider, operation, TTL_GEOCODING_SECONDS, **_rules())
        if not ttl:
            return
        await CacheService.set_json(cache_key, response.model_dump(), ttl)
        if use.may_store:
            await durable_cache.set_geocode(cache_key, kind=kind, response=response.model_dump(mode="json"), ttl_seconds=ttl)
