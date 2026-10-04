"""Weather provider abstraction (Master Blueprint §25-26)."""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Optional


@dataclass(frozen=True)
class CurrentWeather:
    latitude: float
    longitude: float
    temperature_c: float
    condition: str
    humidity: Optional[int]
    wind_kph: Optional[float]
    provider: str
    feels_like_c: Optional[float] = None  # apparent temperature, when the provider reports it


@dataclass(frozen=True)
class ForecastDay:
    date: str  # ISO date
    high_c: float
    low_c: float
    condition: str
    chance_of_rain_pct: Optional[int]


@dataclass(frozen=True)
class WeatherForecast:
    latitude: float
    longitude: float
    days: list[ForecastDay]
    provider: str


class WeatherProvider(ABC):
    @abstractmethod
    async def get_current(self, latitude: float, longitude: float) -> CurrentWeather:
        raise NotImplementedError

    @abstractmethod
    async def get_forecast(self, latitude: float, longitude: float, days: int) -> WeatherForecast:
        raise NotImplementedError
