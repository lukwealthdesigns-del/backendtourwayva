"""
Google Routes API (computeRoutes): distance, duration and (optionally) the path for driving, walking, cycling and transit.

Never fabricates a route: no route is NotFoundError, a failing provider is ProviderUnavailableError.
"""
from __future__ import annotations

from app.core.exceptions import NotFoundError
from app.providers.google_maps.client import google_request
from app.providers.google_maps.parsing import ROUTES_URL, parse_duration_seconds
from app.providers.maps.interface import MapProvider, RouteResult, TravelMode

_MODE = {TravelMode.DRIVING: "DRIVE", TravelMode.WALKING: "WALK", TravelMode.CYCLING: "BICYCLE", TravelMode.TRANSIT: "TRANSIT"}


def _waypoint(lat: float, lon: float) -> dict:
    return {"location": {"latLng": {"latitude": lat, "longitude": lon}}}


class GoogleRoutesProvider(MapProvider):
    async def get_route(
        self, *, origin_lat: float, origin_lon: float, destination_lat: float, destination_lon: float,
        mode: TravelMode, include_polyline: bool = False,
    ) -> RouteResult:
        body = {
            "origin": _waypoint(origin_lat, origin_lon), "destination": _waypoint(destination_lat, destination_lon),
            "travelMode": _MODE[mode], "units": "METRIC",
        }
        if mode == TravelMode.DRIVING:
            body["routingPreference"] = "TRAFFIC_UNAWARE"      # traffic-aware routing is a more expensive tier and is only valid for driving
        mask = "routes.duration,routes.distanceMeters" + (",routes.polyline.encodedPolyline" if include_polyline else "")
        data = await google_request("google_routes", "POST", ROUTES_URL, field_mask=mask, body=body)
        routes = data.get("routes") or []
        if not routes:
            raise NotFoundError("No route could be found between these locations.")
        route = routes[0]
        seconds = parse_duration_seconds(route.get("duration"))
        if seconds is None or route.get("distanceMeters") is None:
            raise NotFoundError("No route could be found between these locations.")
        return RouteResult(
            distance_meters=float(route["distanceMeters"]), duration_seconds=seconds, mode=mode, provider="google_routes",
            polyline=(route.get("polyline") or {}).get("encodedPolyline") if include_polyline else None,
        )
