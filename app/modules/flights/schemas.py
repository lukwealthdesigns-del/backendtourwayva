"""Pydantic schemas for flight search endpoints."""
from __future__ import annotations

from datetime import date
from typing import Optional

from pydantic import BaseModel, Field, model_validator


class FlightSearchRequest(BaseModel):
    origin: str = Field(..., min_length=3, max_length=3, description="IATA code, e.g. 'LOS'")
    destination: str = Field(..., min_length=3, max_length=3, description="IATA code, e.g. 'LHR'")
    departure_date: date
    return_date: Optional[date] = None
    adults: int = Field(default=1, ge=1, le=9)
    cabin: Optional[str] = Field(default=None, description="economy | premium_economy | business | first")
    max_results: int = Field(default=20, ge=1, le=50)

    @model_validator(mode="after")
    def _dates_valid(self) -> "FlightSearchRequest":
        if self.return_date and self.return_date <= self.departure_date:
            raise ValueError("return_date must be after departure_date.")
        return self


class FlightOfferResponse(BaseModel):
    offer_id: str
    origin: str
    destination: str
    departure_time: str
    arrival_time: str
    duration_iso8601: str
    stops: int
    airline_codes: list[str]
    cabin: Optional[str] = None
    price_total: float
    currency: str
    provider: str


class FlightSearchResponse(BaseModel):
    results: list[FlightOfferResponse]
    source: str  # "cache" | "live"
    count: int
