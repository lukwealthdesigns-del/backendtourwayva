"""
WeatherAPI.com provider (the "Weather provider" referenced generically
in the Master Blueprint). Chosen for its single API covering both
current conditions and multi-day forecast.

Docs: https://www.weatherapi.com/docs/

Never fabricates conditions: if not configured or the request fails,
raises ProviderUnavailableError (Master Blueprint §78, §108) — the
caller must degrade gracefully (e.g. skip weather-aware itinerary
adjustments) rather than invent a forecast.
"""
from __future__ import annotations

import httpx

from app.core.config import settings
from app.core.exceptions import ProviderUnavailableError
from app.core.redaction import redact_secrets
from app.core.resilience import CircuitOpenError
from app.providers.http_resilience import resilient_request, safe_error_text
from app.core.logging import get_logger
from app.providers.weather.interface import (
    CurrentWeather,
    ForecastDay,
    WeatherForecast,
    WeatherProvider,
)

logger = get_logger(__name__)

_CURRENT_URL = "https://api.weatherapi.com/v1/current.json"
_FORECAST_URL = "https://api.weatherapi.com/v1/forecast.json"


class WeatherAPIProvider(WeatherProvider):
    async def get_current(self, latitude: float, longitude: float) -> CurrentWeather:
        data = await self._call(_CURRENT_URL, {"q": f"{latitude},{longitude}"})
        current = data["current"]
        return CurrentWeather(
            latitude=latitude,
            longitude=longitude,
            temperature_c=current["temp_c"],
            condition=current["condition"]["text"],
            humidity=current.get("humidity"),
            wind_kph=current.get("wind_kph"),
            provider="weatherapi",
            feels_like_c=current.get("feelslike_c"),
        )

    async def get_forecast(self, latitude: float, longitude: float, days: int) -> WeatherForecast:
        days = max(1, min(days, 10))  # WeatherAPI free/standard tiers cap forecast length
        data = await self._call(_FORECAST_URL, {"q": f"{latitude},{longitude}", "days": days})
        forecast_days = [
            ForecastDay(
                date=day["date"],
                high_c=day["day"]["maxtemp_c"],
                low_c=day["day"]["mintemp_c"],
                condition=day["day"]["condition"]["text"],
                chance_of_rain_pct=day["day"].get("daily_chance_of_rain"),
            )
            for day in data["forecast"]["forecastday"]
        ]
        return WeatherForecast(
            latitude=latitude, longitude=longitude, days=forecast_days, provider="weatherapi"
        )

    async def _call(self, url: str, params: dict) -> dict:
        if not settings.WEATHER_API_KEY:
            raise ProviderUnavailableError("Weather provider is not configured.")

        params = {**params, "key": settings.WEATHER_API_KEY}

        async def attempt() -> dict:
            async with httpx.AsyncClient(timeout=8.0) as client:
                resp = await client.get(url, params=params)
                resp.raise_for_status()
                return resp.json()

        try:
            return await resilient_request("weatherapi", attempt)
        except httpx.HTTPStatusError as exc:
            logger.error("weatherapi_http_error", status=exc.response.status_code, body=safe_error_text(exc))
            raise ProviderUnavailableError("Weather lookup failed. Please try again.") from exc
        except CircuitOpenError:
            raise
        except Exception as exc:  # noqa: BLE001
            logger.error("weatherapi_error", error=redact_secrets(exc))
            raise ProviderUnavailableError("Weather lookup failed. Please try again.") from exc
