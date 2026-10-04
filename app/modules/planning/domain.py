"""Plain dataclasses describing a planned trip. Deliberately free of
framework/ORM imports so the planning rules and workflow can be unit-tested
without a database (and so LLM output never touches an ORM object before it
has been validated — Principle 2)."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, time
from typing import Any, Optional

ITEM_TYPES = ("hotel", "flight", "activity", "restaurant", "attraction", "transport", "note", "custom")
# Item types that can be dropped to fit a budget (never accommodation/transport).
OPTIONAL_COST_TYPES = ("activity", "attraction", "restaurant", "custom")


@dataclass
class PlannedItem:
    item_type: str
    title: str
    description: Optional[str] = None
    location_name: Optional[str] = None
    latitude: Optional[float] = None
    longitude: Optional[float] = None
    start_time: Optional[time] = None
    end_time: Optional[time] = None
    estimated_cost: Optional[float] = None      # for the WHOLE travelling group, in the plan currency
    currency: Optional[str] = None
    provider: Optional[str] = None
    source: str = "estimated"                   # "provider" (live data) | "estimated"
    external_id: Optional[str] = None
    booking_link: Optional[str] = None
    image_url: Optional[str] = None
    notes: Optional[str] = None
    outdoor: bool = False                       # used only for weather-aware checks
    must_see: bool = False                      # user-requested; never dropped to save money
    # OpenStreetMap `opening_hours` string for this venue when one was found and matched by name. Used
    # ONLY by the opening-hours check; never persisted and never shown to the model as fact.
    opening_hours: Optional[str] = None


@dataclass
class PlannedDay:
    day_number: int
    date: date
    items: list[PlannedItem] = field(default_factory=list)
    weather_summary: Optional[str] = None


@dataclass
class PlannedTrip:
    overview: str
    currency: str
    days: list[PlannedDay] = field(default_factory=list)


@dataclass(frozen=True)
class TripSpec:
    destination: str
    start_date: date
    end_date: date
    travelers: int = 1
    origin: Optional[str] = None
    budget_amount: Optional[float] = None
    budget_currency: Optional[str] = None

    @property
    def num_days(self) -> int:
        return (self.end_date - self.start_date).days + 1

    @property
    def nights(self) -> int:
        return max(self.num_days - 1, 0)


@dataclass(frozen=True)
class GeoPoint:
    latitude: float
    longitude: float
    formatted_address: str = ""
    country: Optional[str] = None
    city: Optional[str] = None


@dataclass
class Issue:
    code: str
    severity: str                     # "error" (must be fixed) | "warning" (reported, not blocking)
    message: str
    day_number: Optional[int] = None
    item_index: Optional[int] = None  # index inside that day's item list
    extra: dict[str, Any] = field(default_factory=dict)

    @property
    def is_error(self) -> bool:
        return self.severity == "error"
