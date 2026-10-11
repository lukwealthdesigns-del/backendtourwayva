"""Pydantic schemas for map/routing endpoints."""
from __future__ import annotations

from typing import Optional

from pydantic import BaseModel, Field

from app.providers.maps.interface import TravelMode


class RouteRequest(BaseModel):
    origin_lat: float = Field(..., ge=-90, le=90)
    origin_lon: float = Field(..., ge=-180, le=180)
    destination_lat: float = Field(..., ge=-90, le=90)
    destination_lon: float = Field(..., ge=-180, le=180)
    mode: TravelMode = TravelMode.WALKING
    include_polyline: bool = False   # also return the path (encoded polyline) for drawing it on a map


class RouteResponse(BaseModel):
    distance_meters: float
    duration_seconds: float
    mode: TravelMode
    source: str  # "cache" | "live"
    provider: str
    polyline: Optional[str] = None
