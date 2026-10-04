"""Pydantic schemas for weather endpoints."""
from __future__ import annotations

from typing import Optional

from pydantic import BaseModel, Field


class CurrentWeatherQuery(BaseModel):
    latitude: float = Field(..., ge=-90, le=90)
    longitude: float = Field(..., ge=-180, le=180)


class CurrentWeatherResponse(BaseModel):
    latitude: float
    longitude: float
    temperature_c: float
    condition: str
    humidity: Optional[int] = None
    wind_kph: Optional[float] = None
    feels_like_c: Optional[float] = None  # apparent temperature; absent if the provider does not report it
    source: str  # "cache" | "live"
    provider: str
    # Where the coordinates came from — set by the router, never cached with the weather itself.
    # location_source: "explicit" (the client sent latitude/longitude), "ip" (approximated from
    # the request's IP address) or "account_country" (the country the account was localized to).
    location_source: Optional[str] = None
    city: Optional[str] = None
    region: Optional[str] = None
    country: Optional[str] = None


class ForecastDayResponse(BaseModel):
    date: str
    high_c: float
    low_c: float
    condition: str
    chance_of_rain_pct: Optional[int] = None


class DestinationWeatherQuery(BaseModel):
    latitude: float = Field(..., ge=-90, le=90)
    longitude: float = Field(..., ge=-180, le=180)
    days: int = Field(default=5, ge=1, le=10)


class ForecastResponse(BaseModel):
    latitude: float
    longitude: float
    days: list[ForecastDayResponse]
    source: str
    provider: str
