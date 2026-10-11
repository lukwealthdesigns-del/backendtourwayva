"""Pydantic schemas for hotel search endpoints."""
from __future__ import annotations

from datetime import date
from typing import Optional

from pydantic import BaseModel, Field, model_validator


class HotelSearchRequest(BaseModel):
    city_code: str = Field(..., min_length=3, max_length=3, description="IATA city code, e.g. 'PAR'")
    check_in: date
    check_out: date
    adults: int = Field(default=1, ge=1, le=9)
    max_hotels: int = Field(default=20, ge=1, le=50)

    @model_validator(mode="after")
    def _dates_valid(self) -> "HotelSearchRequest":
        if self.check_out <= self.check_in:
            raise ValueError("check_out must be after check_in.")
        return self


class HotelDiscoverRequest(BaseModel):
    destination: str = Field(..., min_length=2, max_length=255)
    latitude: Optional[float] = Field(default=None, ge=-90, le=90)
    longitude: Optional[float] = Field(default=None, ge=-180, le=180)
    check_in: Optional[date] = None
    check_out: Optional[date] = None
    max_hotels: int = Field(default=20, ge=1, le=20)


class HotelOfferResponse(BaseModel):
    hotel_id: str
    hotel_name: str
    offer_id: Optional[str] = None
    latitude: Optional[float] = None
    longitude: Optional[float] = None
    check_in: Optional[date] = None
    check_out: Optional[date] = None
    room_description: Optional[str] = None
    price_total: Optional[float] = None     # None for discovery results: no room or price is claimed
    currency: Optional[str] = None
    provider: str
    # Discovery + provenance (see app/providers/travel/status.py)
    kind: str = "inventory"                 # "inventory" | "discovery"
    address: Optional[str] = None
    rating: Optional[float] = None
    user_rating_count: Optional[int] = None
    photo_url: Optional[str] = None
    website: Optional[str] = None
    maps_url: Optional[str] = None
    price_level: Optional[str] = None
    provider_reference: Optional[str] = None
    data_status: Optional[str] = None
    last_verified_at: Optional[str] = None
    expires_at: Optional[str] = None
    source_url: Optional[str] = None


class HotelSearchResponse(BaseModel):
    results: list[HotelOfferResponse]
    source: str  # "cache" | "live"
    count: int
