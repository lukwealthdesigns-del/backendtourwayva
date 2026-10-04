"""Pydantic schemas for activity search endpoints."""
from __future__ import annotations

from typing import Optional

from pydantic import BaseModel, Field


class ActivitySearchRequest(BaseModel):
    latitude: float = Field(..., ge=-90, le=90)
    longitude: float = Field(..., ge=-180, le=180)
    radius_km: int = Field(default=5, ge=1, le=50)


class ActivityResponse(BaseModel):
    activity_id: str
    name: str
    description: Optional[str] = None
    latitude: float
    longitude: float
    price_amount: Optional[float] = None
    currency: Optional[str] = None
    picture_url: Optional[str] = None
    booking_link: Optional[str] = None
    provider: str


class ActivitySearchResponse(BaseModel):
    results: list[ActivityResponse]
    source: str  # "cache" | "live"
    count: int
