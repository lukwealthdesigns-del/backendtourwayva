"""Pydantic schemas for Discover endpoints (Master Blueprint §10-12)."""
from __future__ import annotations

import uuid
from datetime import date
from typing import Optional

from pydantic import BaseModel, Field, model_validator


class DiscoverSearchRequest(BaseModel):
    budget_amount: float = Field(..., gt=0)
    budget_currency: str = Field(..., min_length=3, max_length=3)
    origin: Optional[str] = Field(default=None, max_length=200)
    continent: Optional[str] = Field(default=None, max_length=50)
    country_region: Optional[str] = Field(default=None, max_length=100)
    start_date: Optional[date] = None
    end_date: Optional[date] = None
    duration_days: int = Field(default=7, ge=1, le=90)
    travelers: int = Field(default=1, ge=1, le=20)
    travel_style: Optional[str] = Field(default=None, max_length=100)
    interests: list[str] = Field(default_factory=list)
    climate_preference: Optional[str] = Field(default=None, max_length=50)
    accommodation_preference: Optional[str] = Field(default=None, max_length=100)
    transportation_preference: Optional[str] = Field(default=None, max_length=100)
    max_results: int = Field(default=5, ge=1, le=10)
    # Leave out places the user's travel history says they have already visited.
    exclude_visited: bool = True

    @model_validator(mode="after")
    def _dates_valid(self) -> "DiscoverSearchRequest":
        if self.start_date and self.end_date:
            if self.end_date < self.start_date:
                raise ValueError("end_date cannot be before start_date.")
            self.duration_days = (self.end_date - self.start_date).days + 1
        return self


class CostBreakdown(BaseModel):
    accommodation: float
    food: float
    transport: float
    activities: float
    total: float
    currency: str
    source: str = "estimated"  # always "estimated" — Discover never claims live pricing


class DiscoverResultItem(BaseModel):
    destination: str
    country: Optional[str] = None
    latitude: Optional[float] = None
    longitude: Optional[float] = None
    cost_breakdown: CostBreakdown
    suggested_duration_days: int
    weather_summary: Optional[str] = None
    best_travel_period: str
    reasons: str
    image_url: Optional[str] = None
    image_attribution: Optional[str] = None
    relevant_activities: list[str] = Field(default_factory=list)
    over_budget: bool
    trip_planning_cta: dict  # prefillable payload for POST /trips
    score: Optional[float] = None          # overall ranking score (0-1)
    season_fit: Optional[float] = None     # share of the trip that falls in the best months; None = unknown


class DiscoverSearchResponse(BaseModel):
    search_id: uuid.UUID
    results: list[DiscoverResultItem]
    source: str  # "cache" | "live"
    notes: list[str] = Field(default_factory=list)   # personalization applied, places left out, estimate caveats
