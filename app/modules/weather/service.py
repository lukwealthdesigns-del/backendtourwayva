"""
WeatherService — implements "reuse before regenerate" (Blueprint §3,
§25-26): checks the Redis cache before calling the weather provider,
with a SHORT TTL since weather changes fast (unlike geocoding).
"""
from __future__ import annotations

from app.modules.weather.schemas import (
    CurrentWeatherResponse,
    ForecastDayResponse,
    ForecastResponse,
)
from app.providers.weather.weatherapi_provider import WeatherAPIProvider
from app.services.cache_service import (
    TTL_WEATHER_CURRENT_SECONDS,
    TTL_WEATHER_FORECAST_SECONDS,
    CacheService,
    weather_current_cache_key,
    weather_forecast_cache_key,
)

_provider = WeatherAPIProvider()


class WeatherService:
    async def get_current(self, latitude: float, longitude: float) -> CurrentWeatherResponse:
        cache_key = weather_current_cache_key(latitude, longitude)
        cached = await CacheService.get_json(cache_key)
        if cached is not None:
            return CurrentWeatherResponse(**{**cached, "source": "cache"})

        result = await _provider.get_current(latitude, longitude)
        response = CurrentWeatherResponse(
            latitude=result.latitude,
            longitude=result.longitude,
            temperature_c=result.temperature_c,
            condition=result.condition,
            humidity=result.humidity,
            wind_kph=result.wind_kph,
            feels_like_c=result.feels_like_c,
            source="live",
            provider=result.provider,
        )
        await CacheService.set_json(cache_key, response.model_dump(), TTL_WEATHER_CURRENT_SECONDS)
        return response

    async def get_forecast(self, latitude: float, longitude: float, days: int) -> ForecastResponse:
        # Cache keyed by a coarse "today" bucket handled inside the key helper
        # (see weather_forecast_cache_key) — here we key on lat/lon/day-count.
        from datetime import date

        cache_key = weather_forecast_cache_key(latitude, longitude, f"{date.today().isoformat()}:{days}")
        cached = await CacheService.get_json(cache_key)
        if cached is not None:
            return ForecastResponse(**{**cached, "source": "cache"})

        result = await _provider.get_forecast(latitude, longitude, days)
        response = ForecastResponse(
            latitude=result.latitude,
            longitude=result.longitude,
            days=[
                ForecastDayResponse(
                    date=d.date, high_c=d.high_c, low_c=d.low_c,
                    condition=d.condition, chance_of_rain_pct=d.chance_of_rain_pct,
                )
                for d in result.days
            ],
            source="live",
            provider=result.provider,
        )
        await CacheService.set_json(cache_key, response.model_dump(), TTL_WEATHER_FORECAST_SECONDS)
        return response
