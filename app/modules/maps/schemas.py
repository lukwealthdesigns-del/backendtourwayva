"""Pydantic schemas for map/routing endpoints."""
from __future__ import annotations

from pydantic import BaseModel, Field

from app.providers.maps.interface import TravelMode


class RouteRequest(BaseModel):
    origin_lat: float = Field(..., ge=-90, le=90)
    origin_lon: float = Field(..., ge=-180, le=180)
    destination_lat: float = Field(..., ge=-90, le=90)
    destination_lon: float = Field(..., ge=-180, le=180)
    mode: TravelMode = TravelMode.WALKING


class RouteResponse(BaseModel):
    distance_meters: float
    duration_seconds: float
    mode: TravelMode
    source: str  # "cache" | "live"
    provider: str
