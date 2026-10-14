"""
Is a route between two points worth asking a routing provider for? (stdlib only, unit-tested without the web stack)

Routing providers refuse very long routes (OpenRouteService: a driving route may not exceed 6,000 km) and walking/cycling routes
across a continent are meaningless. Sending those only produced provider errors (HTTP 400, then a 503 to the app). A route that
cannot exist is answered at once, as "no route", and the app shows the direct distance (a flight) instead.
"""
from __future__ import annotations

import math

# Straight-line limits in km. Driving: the road route is typically 1.2-1.4x the straight line, so 4,000 km keeps it under 6,000 km.
MAX_STRAIGHT_KM = {"driving": 4000.0, "transit": 4000.0, "cycling": 400.0, "walking": 60.0}


def haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    r = 6371.0088
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dphi, dl = p2 - p1, math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(min(1.0, math.sqrt(a)))


def valid_coordinates(lat: float, lon: float) -> bool:
    return -90.0 <= lat <= 90.0 and -180.0 <= lon <= 180.0


def route_too_far(mode: str, straight_km: float) -> bool:
    return straight_km > MAX_STRAIGHT_KM.get(mode, 4000.0)


# OpenRouteService error codes that mean "this route cannot exist" (not "the service is down"): too long (2004), route not found (2009),
# a point is not near a road (2010).
ORS_NO_ROUTE_CODES = frozenset({2004, 2009, 2010})
