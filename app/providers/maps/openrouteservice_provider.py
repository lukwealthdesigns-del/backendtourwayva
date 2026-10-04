"""
OpenRouteService (ORS) map/routing provider.

Docs: https://openrouteservice.org/dev/#/api-docs

Chosen for the Maps/Routing abstraction because it has a generous
free tier and supports the walking/driving/cycling modes needed for
itinerary geographic-efficiency scoring (Blueprint §16, §30) without
requiring a billing account, unlike some commercial map platforms.
Transit routing is NOT supported by ORS's free tier — requesting it
raises a controlled error rather than silently falling back to
another mode.

Never fabricates a route: raises ProviderUnavailableError if not
configured or the request fails (Blueprint §78, §108).
"""
from __future__ import annotations

import httpx

from app.core.config import settings
from app.core.redaction import redact_secrets
from app.core.resilience import CircuitOpenError
from app.providers.http_resilience import resilient_request, safe_error_text
from app.core.exceptions import NotFoundError, ProviderUnavailableError, ValidationAppError
from app.core.logging import get_logger
from app.providers.maps.interface import MapProvider, RouteResult, TravelMode

logger = get_logger(__name__)

_BASE_URL = "https://api.openrouteservice.org/v2/directions"

_ORS_PROFILE = {
    TravelMode.WALKING: "foot-walking",
    TravelMode.DRIVING: "driving-car",
    TravelMode.CYCLING: "cycling-regular",
}


class OpenRouteServiceProvider(MapProvider):
    async def get_route(
        self,
        *,
        origin_lat: float,
        origin_lon: float,
        destination_lat: float,
        destination_lon: float,
        mode: TravelMode,
    ) -> RouteResult:
        if mode == TravelMode.TRANSIT:
            raise ValidationAppError("Transit routing is not supported by the configured maps provider.")

        if not settings.ORS_API_KEY:
            raise ProviderUnavailableError("Maps/routing provider is not configured.")

        profile = _ORS_PROFILE[mode]
        url = f"{_BASE_URL}/{profile}"
        headers = {"Authorization": settings.ORS_API_KEY, "Content-Type": "application/json"}
        body = {"coordinates": [[origin_lon, origin_lat], [destination_lon, destination_lat]]}

        async def attempt() -> dict:
            async with httpx.AsyncClient(timeout=10.0) as client:
                resp = await client.post(url, json=body, headers=headers)
                resp.raise_for_status()
                return resp.json()

        try:
            data = await resilient_request("openrouteservice", attempt)
        except httpx.HTTPStatusError as exc:
            logger.error("ors_http_error", status=exc.response.status_code, body=safe_error_text(exc))
            raise ProviderUnavailableError("Route calculation failed. Please try again.") from exc
        except CircuitOpenError:
            raise
        except Exception as exc:  # noqa: BLE001
            logger.error("ors_error", error=redact_secrets(exc))
            raise ProviderUnavailableError("Route calculation failed. Please try again.") from exc

        routes = data.get("routes") or []
        if not routes:
            raise NotFoundError("No route could be found between these locations.")

        summary = routes[0]["summary"]
        return RouteResult(
            distance_meters=summary["distance"],
            duration_seconds=summary["duration"],
            mode=mode,
            provider="openrouteservice",
        )
