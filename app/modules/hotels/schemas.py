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


class HotelOfferResponse(BaseModel):
    hotel_id: str
    hotel_name: str
    offer_id: str
    latitude: Optional[float] = None
    longitude: Optional[float] = None
    check_in: date
    check_out: date
    room_description: Optional[str] = None
    price_total: float
    currency: str
    provider: str


class HotelSearchResponse(BaseModel):
    results: list[HotelOfferResponse]
    source: str  # "cache" | "live"
    count: int
