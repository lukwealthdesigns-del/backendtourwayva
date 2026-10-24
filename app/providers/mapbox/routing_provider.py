"""
Mapbox Directions API v5 as a MapProvider (distance, travel time and, when asked for, the path to draw).

Docs: https://docs.mapbox.com/api/navigation/directions/
Profiles: driving, walking, cycling. Mapbox has no transit profile, so TRANSIT is refused with a controlled error and the
service falls through to the next provider. The path is returned as a 5-digit-precision encoded polyline (Mapbox's default),
the same encoding the rest of the app already decodes. Routes are never stored (see app/core/location_policy.py).
"""
from __future__ import annotations

import httpx

from app.core.exceptions import NotFoundError, ProviderUnavailableError, ValidationAppError
from app.core.logging import get_logger
from app.core.redaction import redact_secrets
from app.core.resilience import CircuitOpenError
from app.providers.http_resilience import safe_error_text
from app.providers.maps.interface import MapProvider, RouteResult, TravelMode
from app.providers.mapbox import client
from app.providers.mapbox.parsing import parse_directions

logger = get_logger(__name__)

_PROFILE = {TravelMode.WALKING: "walking", TravelMode.DRIVING: "driving", TravelMode.CYCLING: "cycling"}


def _error_code(exc: "httpx.HTTPStatusError"):
    try:
        return exc.response.json().get("code")
    except Exception:  # noqa: BLE001
        return None


class MapboxDirectionsProvider(MapProvider):
    async def get_route(self, *, origin_lat: float, origin_lon: float, destination_lat: float, destination_lon: float,
                        mode: TravelMode, include_polyline: bool = False) -> RouteResult:
        if mode not in _PROFILE:
            raise ValidationAppError("Transit routing is not supported by the configured maps provider.")
        url = f"{client.DIRECTIONS_BASE}/{_PROFILE[mode]}/{origin_lon:.6f},{origin_lat:.6f};{destination_lon:.6f},{destination_lat:.6f}"
        params = {"overview": "full" if include_polyline else "false", "geometries": "polyline", "steps": "false", "alternatives": "false"}
        try:
            data = await client.get_json("mapbox_directions", url, params, timeout=10.0)
        except httpx.HTTPStatusError as exc:
            status = exc.response.status_code
            text = exc.response.text.lower()
            if _error_code(exc) in ("NoRoute", "NoSegment") or (status == 422 and ("distance" in text or "limit" in text)):
                # Mapbox says the route cannot exist (no road, or beyond its range): an answer, not an outage.
                raise NotFoundError("No route could be found between these locations.") from exc
            logger.error("mapbox_directions_http_error", status=status, body=safe_error_text(exc))
            raise ProviderUnavailableError("Route calculation failed. Please try again.") from exc
        except (CircuitOpenError, ProviderUnavailableError):
            raise
        except Exception as exc:  # noqa: BLE001
            logger.error("mapbox_directions_error", error=redact_secrets(exc))
            raise ProviderUnavailableError("Route calculation failed. Please try again.") from exc
        distance, duration, geometry = parse_directions(data)
        return RouteResult(distance_meters=distance, duration_seconds=duration, mode=mode, provider="mapbox",
                           polyline=geometry if include_polyline else None)
