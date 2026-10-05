"""Pydantic schemas for trip endpoints."""
from __future__ import annotations

import uuid
from datetime import date, datetime
from typing import Optional

from pydantic import BaseModel, Field, model_validator

from app.core.constants import TripMemberRole, TripStatus


class TripCreateRequest(BaseModel):
    title: str = Field(..., min_length=1, max_length=200)
    origin: Optional[str] = Field(default=None, max_length=200)
    destination: str = Field(..., min_length=1, max_length=200)
    start_date: date
    end_date: date
    travelers: int = Field(default=1, ge=1, le=50)
    budget_amount: Optional[float] = Field(default=None, gt=0)
    budget_currency: Optional[str] = Field(default=None, min_length=3, max_length=3)

    @model_validator(mode="after")
    def _dates_valid(self) -> "TripCreateRequest":
        if self.end_date < self.start_date:
            raise ValueError("end_date cannot be before start_date.")
        # A generous but real ceiling — prevents accidental
        # multi-year date ranges from scaffolding thousands of days.
        if (self.end_date - self.start_date).days > 90:
            raise ValueError("Trips longer than 90 days are not supported yet.")
        return self


class TripUpdateRequest(BaseModel):
    """Partial update of a trip's basics. Send only what changes. Destination and dates can only change while the trip
    has no itinerary yet (otherwise the plan would no longer match them); everything else is always editable."""

    model_config = {"extra": "forbid"}

    title: Optional[str] = Field(default=None, min_length=1, max_length=200)
    origin: Optional[str] = Field(default=None, max_length=200)
    destination: Optional[str] = Field(default=None, min_length=1, max_length=200)
    start_date: Optional[date] = None
    end_date: Optional[date] = None
    travelers: Optional[int] = Field(default=None, ge=1, le=50)
    budget_amount: Optional[float] = Field(default=None, gt=0)
    budget_currency: Optional[str] = Field(default=None, min_length=3, max_length=3)

    @model_validator(mode="after")
    def _something_to_change(self) -> "TripUpdateRequest":
        if not self.model_fields_set:
            raise ValueError("Send at least one field to change.")
        if self.start_date and self.end_date and self.end_date < self.start_date:
            raise ValueError("end_date cannot be before start_date.")
        return self


class TripGenerationInfo(BaseModel):
    """Where itinerary generation stands for a trip (only present while generating or after a
    failed attempt). `job_id` can be polled at GET /trips/{id}/generate/{job_id}."""

    job_id: uuid.UUID
    state: str                       # "generating" | "failed"
    error_code: Optional[str] = None
    error_message: Optional[str] = None
    retryable: Optional[bool] = None


class TripResponse(BaseModel):
    id: uuid.UUID
    owner_id: uuid.UUID
    title: str
    origin: Optional[str] = None
    destination: str
    start_date: date
    end_date: date
    travelers: int
    budget_amount: Optional[float] = None
    budget_currency: Optional[str] = None
    status: TripStatus
    # AI/system-written summary of the trip (set when an itinerary is generated).
    overview: Optional[str] = None
    current_version_number: int
    created_at: datetime

    # --- Backend-authoritative status for the UI (see app/modules/trips/display_status.py) ---
    # `status` above is the raw lifecycle column; `display_status` is what the badge should show:
    # draft | generating | failed | ready | upcoming | active | completed | cancelled | archived.
    # `bucket` is the Trips-page tab: drafts | upcoming | active | completed | archived.
    # They are filled in by app/modules/trips/presenters.py for the VIEWING member.
    display_status: Optional[str] = None
    bucket: Optional[str] = None
    is_archived: bool = False                       # per member: archiving only hides it for you
    archived_at: Optional[datetime] = None
    generation: Optional[TripGenerationInfo] = None  # present while generating, or after a failure
    updated_at: Optional[datetime] = None            # last change (for "last updated" on trip cards)

    model_config = {"from_attributes": True}


# Single source of truth for the member shape (now includes the member's public profile).
from app.modules.collaboration.schemas import TripMemberResponse  # noqa: E402,F401


class ReplaceHotelRequest(BaseModel):
    model_config = {"extra": "forbid"}

    new_hotel_id: str = Field(..., min_length=1, max_length=100, description="Amadeus hotel_id from a hotel search result")


class ReplaceHotelResponse(BaseModel):
    item_id: uuid.UUID
    title: str
    previous_cost: Optional[float] = None
    new_cost: Optional[float] = None
    currency: str
    cost_delta: Optional[float] = Field(default=None, description="Positive = the new hotel costs more")
    version_number: int


class AddFlightRequest(BaseModel):
    """The exact offer fields from a POST /flights/search result the user just chose."""

    model_config = {"extra": "forbid"}

    day_id: uuid.UUID = Field(..., description="Which trip day this flight departs on")
    offer_id: str = Field(..., min_length=1, max_length=100)
    origin: str = Field(..., min_length=3, max_length=3)
    destination: str = Field(..., min_length=3, max_length=3)
    departure_time: str = Field(..., description="ISO 8601, e.g. '2026-10-01T08:30:00'")
    arrival_time: str = Field(..., description="ISO 8601")
    duration_iso8601: str = Field(..., max_length=20)
    stops: int = Field(..., ge=0, le=5)
    airline_codes: list[str] = Field(..., min_length=1, max_length=5)
    price_total: float = Field(..., gt=0)
    currency: str = Field(..., min_length=3, max_length=3)
    provider: str = Field(..., min_length=1, max_length=50)
    cabin: Optional[str] = Field(default=None, max_length=30)


class AddFlightResponse(BaseModel):
    item_id: uuid.UUID
    title: str
    estimated_cost: float
    currency: str
    version_number: int
    price_verified: bool = Field(
        description="True if price/availability was just re-confirmed live with the airline "
        "(Amadeus Flight Offers Price). False means Amadeus could not be reached to re-confirm "
        "and the figures are from your search a moment ago — see verification_note."
    )
    verification_note: Optional[str] = None


class BookingClickRequest(BaseModel):
    """Sent when the user clicks "Book this" / follows a trip item's
    booking_link out to the provider. Tracked for the admin dashboard's
    ESTIMATED affiliate-revenue reporting (Blueprint §57) — never a
    confirmed booking or payout."""

    estimated_value_usd: float = Field(
        ge=0, description="The item's estimated_cost converted to USD by the CLIENT (it already has the live rate on screen)."
    )


class BookingClickResponse(BaseModel):
    recorded: bool = True
