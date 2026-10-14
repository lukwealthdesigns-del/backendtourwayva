"""
MapsService - "reuse before regenerate" for routing (Blueprint §3, §30): road-network distance/duration between two fixed
points rarely changes, so results are cached with a medium-long TTL. Providers come from the resolver (Google Routes first,
OpenRouteService when configured); an unavailable or unsupporting provider falls through to the next one, while "no route
exists" ends the search.
"""
from __future__ import annotations

from typing import Optional

from app.core.exceptions import NotFoundError, ProviderUnavailableError, ValidationAppError
from app.modules.maps.limits import haversine_km, route_too_far, valid_coordinates
from app.modules.maps.schemas import RouteResponse
from app.providers.maps.interface import TravelMode
from app.providers.travel.registry import get_resolver
from app.providers.travel.resolver import Capability
from app.services.cache_service import TTL_ROUTE_SECONDS, CacheService, route_cache_key


class MapsService:
    async def get_route(
        self,
        *,
        origin_lat: float,
        origin_lon: float,
        destination_lat: float,
        destination_lon: float,
        mode: TravelMode,
        include_polyline: bool = False,
    ) -> RouteResponse:
        if not (valid_coordinates(origin_lat, origin_lon) and valid_coordinates(destination_lat, destination_lon)):
            raise ValidationAppError("Those coordinates are not valid (latitude -90 to 90, longitude -180 to 180).")
        straight_km = haversine_km(origin_lat, origin_lon, destination_lat, destination_lon)
        if route_too_far(mode.value, straight_km):
            # No provider can (or should) route this: answer at once with "no route" instead of a failing provider call.
            raise NotFoundError(f"No {mode.value} route exists between these places (about {round(straight_km):,} km apart).")
        cache_key = route_cache_key(origin_lat, origin_lon, destination_lat, destination_lon, mode.value)
        if include_polyline:
            cache_key += ":path"

        cached = await CacheService.get_json(cache_key)
        if cached is not None:
            return RouteResponse(**{**cached, "source": "cache"})

        providers = get_resolver().providers(Capability.ROUTING)
        if not providers:
            raise ProviderUnavailableError("Maps/routing provider is not configured.")
        last_error: Optional[Exception] = None
        result = None
        for _name, provider in providers:
            try:
                result = await provider.get_route(
                    origin_lat=origin_lat, origin_lon=origin_lon, destination_lat=destination_lat,
                    destination_lon=destination_lon, mode=mode, include_polyline=include_polyline,
                )
                break
            except (ProviderUnavailableError, ValidationAppError) as exc:   # try the next provider
                last_error = exc
        if result is None:
            raise last_error or ProviderUnavailableError("Route calculation failed.")
        response = RouteResponse(
            distance_meters=result.distance_meters,
            duration_seconds=result.duration_seconds,
            mode=result.mode,
            source="live",
            provider=result.provider,
            polyline=result.polyline,
        )
        await CacheService.set_json(cache_key, response.model_dump(mode="json"), TTL_ROUTE_SECONDS)
        return response
