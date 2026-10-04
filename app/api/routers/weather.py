"""Weather endpoints (Master Blueprint §84-85)."""
from __future__ import annotations

from fastapi import APIRouter, Depends, Query

from app.api.dependencies import get_client_ip, get_current_user, require_feature, rate_limit
from app.core.constants import FeatureFlag
from app.core.exceptions import ValidationAppError
from app.db.models.user import User
from app.modules.location.service import ApproximateLocationService
from app.modules.weather.schemas import CurrentWeatherResponse, ForecastResponse
from app.modules.weather.service import WeatherService

router = APIRouter(prefix="/weather", tags=["Weather"])
_service = WeatherService()
_locator = ApproximateLocationService()


@router.get("/current", response_model=CurrentWeatherResponse, dependencies=[Depends(rate_limit(bucket="weather:current", max_requests=60, window_seconds=300, per="user")), Depends(require_feature(FeatureFlag.WEATHER))])
async def current_weather(
    latitude: float | None = Query(default=None, ge=-90, le=90),
    longitude: float | None = Query(default=None, ge=-180, le=180),
    current_user: User = Depends(get_current_user),
    client_ip: str | None = Depends(get_client_ip),
):
    """
    Dashboard "current weather" widget.

    - With `latitude` AND `longitude` (e.g. from the browser's geolocation after the user grants
      permission): weather for exactly that point (`location_source: "explicit"`).
    - With neither: the location is approximated server-side — from the request's IP address
      (`"ip"`), falling back to the country the account was localized to (`"account_country"`).
      See `GET /location/approximate` for the same lookup on its own. If nothing can be resolved
      the response is 422 `location_unavailable`: ask the user to allow location access.

    Approximate only, evaluated per request, never stored or tracked (Blueprint §9, §84).
    """
    if (latitude is None) != (longitude is None):
        raise ValidationAppError("Send both latitude and longitude, or neither.")

    if latitude is not None and longitude is not None:
        weather = await _service.get_current(latitude, longitude)
        return weather.model_copy(update={"location_source": "explicit"})

    location = await _locator.resolve(user=current_user, client_ip=client_ip)
    weather = await _service.get_current(location.latitude, location.longitude)
    return weather.model_copy(update={
        "location_source": location.source,
        "city": location.city,
        "region": location.region,
        "country": location.country,
    })


@router.get("/destination", response_model=ForecastResponse, dependencies=[Depends(rate_limit(bucket="weather:destination", max_requests=60, window_seconds=300, per="user")), Depends(require_feature(FeatureFlag.WEATHER))])
async def destination_weather(
    latitude: float = Query(..., ge=-90, le=90),
    longitude: float = Query(..., ge=-180, le=180),
    days: int = Query(default=5, ge=1, le=10),
    current_user: User = Depends(get_current_user),
):
    """Forecast for a destination — used by Discover and Planning to
    reason about trip suitability and weather-aware scheduling."""
    return await _service.get_forecast(latitude, longitude, days)
