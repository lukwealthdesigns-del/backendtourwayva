"""Pydantic schemas for geocoding endpoints."""
from __future__ import annotations

from typing import Optional

from pydantic import BaseModel, Field


class ForwardGeocodeRequest(BaseModel):
    query: str = Field(..., min_length=2, max_length=255, description="Place name or address")


class ReverseGeocodeRequest(BaseModel):
    latitude: float = Field(..., ge=-90, le=90)
    longitude: float = Field(..., ge=-180, le=180)


class GeocodeResponse(BaseModel):
    formatted_address: str
    latitude: float
    longitude: float
    country: Optional[str] = None
    city: Optional[str] = None
    region: Optional[str] = None
    source: str  # "cache" | "live"
    provider: str


class LocationSuggestRequest(BaseModel):
    query: str = Field(..., min_length=2, max_length=100, description="What the user has typed so far (2+ characters).")
    limit: int = Field(default=6, ge=1, le=10)


class LocationSuggestion(BaseModel):
    """One row of the location dropdown: a city, region or country anywhere in the world."""

    formatted_address: str
    latitude: float
    longitude: float
    country: Optional[str] = Field(default=None, description="ISO 3166-1 alpha-2 code")
    city: Optional[str] = None
    region: Optional[str] = None


class LocationSuggestResponse(BaseModel):
    results: list[LocationSuggestion]
    source: str = Field(description="'live' (provider) or 'cache'")
