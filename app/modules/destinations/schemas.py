"""Destination guide: a short, structured overview of any place, for the app's "View details" page."""
from __future__ import annotations

from typing import Literal, Optional

from pydantic import BaseModel, Field


class DestinationGuideRequest(BaseModel):
    name: str = Field(..., min_length=2, max_length=100, description="City, region or country, e.g. 'Lisbon'.")
    country: Optional[str] = Field(default=None, max_length=100, description="Helps disambiguate, e.g. 'Portugal'.")


class DestinationGuide(BaseModel):
    """General guide content, NOT live data. Prices/availability/weather come from the planner and providers."""

    name: str
    country: Optional[str] = None
    country_code: Optional[str] = Field(default=None, description="ISO 3166-1 alpha-2")
    formatted_address: str
    latitude: float
    longitude: float
    overview: str
    best_time: str
    suggested_days: str
    daily_budget_usd: int = Field(description="Rough mid-range spend per person per day, excluding flights (estimate).")
    highlights: list[str]
    languages: Optional[str] = None
    currency_code: Optional[str] = Field(default=None, description="ISO 4217")
    tips: list[str] = Field(default_factory=list)
    source: Literal["ai"] = "ai"
    cached: bool = False
    disclaimer: str = (
        "AI-written overview with general estimates; it can be wrong or out of date. Check official sources for entry "
        "requirements, safety and prices before you travel."
    )
