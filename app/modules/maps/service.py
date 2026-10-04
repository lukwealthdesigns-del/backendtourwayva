"""
MapsService — implements "reuse before regenerate" for routing
(Blueprint §3, §30): road-network distance/duration between two
fixed points rarely changes day-to-day, so results are cached with a
medium-long TTL.
"""
from __future__ import annotations

from app.modules.maps.schemas import RouteResponse
from app.providers.maps.interface import TravelMode
from app.providers.maps.openrouteservice_provider import OpenRouteServiceProvider
from app.services.cache_service import TTL_ROUTE_SECONDS, CacheService, route_cache_key

_provider = OpenRouteServiceProvider()


class MapsService:
    async def get_route(
        self,
        *,
        origin_lat: float,
        origin_lon: float,
        destination_lat: float,
        destination_lon: float,
        mode: TravelMode,
    ) -> RouteResponse:
        cache_key = route_cache_key(origin_lat, origin_lon, destination_lat, destination_lon, mode.value)

        cached = await CacheService.get_json(cache_key)
        if cached is not None:
            return RouteResponse(**{**cached, "source": "cache"})

        result = await _provider.get_route(
            origin_lat=origin_lat,
            origin_lon=origin_lon,
            destination_lat=destination_lat,
            destination_lon=destination_lon,
            mode=mode,
        )
        response = RouteResponse(
            distance_meters=result.distance_meters,
            duration_seconds=result.duration_seconds,
            mode=result.mode,
            source="live",
            provider=result.provider,
        )
        await CacheService.set_json(cache_key, response.model_dump(mode="json"), TTL_ROUTE_SECONDS)
        return response
