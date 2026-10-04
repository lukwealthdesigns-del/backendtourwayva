"""
GeocodingService — implements "reuse before regenerate" (Blueprint
§3, §29): checks the cache before calling OpenCage, and normalizes
the query key so equivalent lookups ("Paris, France" vs "paris,
france") share a cache entry.
"""
from __future__ import annotations

from app.modules.geocoding.schemas import GeocodeResponse, LocationSuggestion, LocationSuggestResponse
from app.providers.geocoding.interface import GeocodeResult
from app.providers.geocoding.opencage_provider import OpenCageProvider
from app.services import durable_cache
from app.services.cache_service import (
    TTL_GEOCODING_SECONDS,
    CacheService,
    geocode_cache_key,
    reverse_geocode_cache_key,
)

_provider = OpenCageProvider()


def _normalize_query(query: str) -> str:
    return " ".join(query.strip().lower().split())


def _result_to_response(result: GeocodeResult, *, source: str) -> GeocodeResponse:
    return GeocodeResponse(
        formatted_address=result.formatted_address,
        latitude=result.latitude,
        longitude=result.longitude,
        country=result.country,
        city=result.city,
        region=result.region,
        source=source,
        provider=result.provider,
    )


def suggest_cache_key(normalized_query: str, limit: int) -> str:
    return f"geosuggest:{limit}:{normalized_query}"


class GeocodingService:
    async def suggest(self, query: str, limit: int = 6) -> LocationSuggestResponse:
        """Autocomplete for location pickers. Cached per normalized query (typing 'lag' then 'lago' then 'lagos'
        by many users should not each cost a provider call). Returns an empty list, never a 404, for no matches."""
        normalized = _normalize_query(query)
        cache_key = suggest_cache_key(normalized, limit)

        cached = await CacheService.get_json(cache_key)
        if cached is not None:
            return LocationSuggestResponse(results=cached, source="cache")

        found = await _provider.search_places(query, limit)
        results = [
            LocationSuggestion(
                formatted_address=r.formatted_address, latitude=r.latitude, longitude=r.longitude,
                country=r.country, city=r.city, region=r.region,
            )
            for r in found
        ]
        await CacheService.set_json(cache_key, [x.model_dump() for x in results], TTL_GEOCODING_SECONDS)
        return LocationSuggestResponse(results=results, source="live")

    async def forward_geocode(self, query: str) -> GeocodeResponse:
        normalized = _normalize_query(query)
        cache_key = geocode_cache_key(normalized)

        cached = await CacheService.get_json(cache_key)
        if cached is not None:
            return GeocodeResponse(**{**cached, "source": "cache"})

        durable = await durable_cache.get_geocode(cache_key)
        if durable is not None:
            response = GeocodeResponse(**{**durable, "source": "cache"})
            await CacheService.set_json(cache_key, response.model_dump(), TTL_GEOCODING_SECONDS)
            return response

        result = await _provider.forward_geocode(query)
        response = _result_to_response(result, source="live")
        await CacheService.set_json(cache_key, response.model_dump(), TTL_GEOCODING_SECONDS)
        await durable_cache.set_geocode(cache_key, kind="forward", response=response.model_dump(mode="json"),
                                        ttl_seconds=TTL_GEOCODING_SECONDS)
        return response

    async def reverse_geocode(self, latitude: float, longitude: float) -> GeocodeResponse:
        cache_key = reverse_geocode_cache_key(latitude, longitude)

        cached = await CacheService.get_json(cache_key)
        if cached is not None:
            return GeocodeResponse(**{**cached, "source": "cache"})

        durable = await durable_cache.get_geocode(cache_key)
        if durable is not None:
            response = GeocodeResponse(**{**durable, "source": "cache"})
            await CacheService.set_json(cache_key, response.model_dump(), TTL_GEOCODING_SECONDS)
            return response

        result = await _provider.reverse_geocode(latitude, longitude)
        response = _result_to_response(result, source="live")
        await CacheService.set_json(cache_key, response.model_dump(), TTL_GEOCODING_SECONDS)
        await durable_cache.set_geocode(cache_key, kind="reverse", response=response.model_dump(mode="json"),
                                        ttl_seconds=TTL_GEOCODING_SECONDS)
        return response
