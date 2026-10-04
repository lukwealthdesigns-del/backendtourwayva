"""Pydantic schemas for itinerary (trip day / trip item) endpoints."""
from __future__ import annotations

import uuid
from datetime import date, time
from typing import Optional

from pydantic import BaseModel, Field, model_validator

from app.core.constants import TripItemType


class TripItemCreateRequest(BaseModel):
    item_type: TripItemType
    title: str = Field(..., min_length=1, max_length=255)
    description: Optional[str] = Field(default=None, max_length=2000)
    location_name: Optional[str] = Field(default=None, max_length=255)
    latitude: Optional[float] = Field(default=None, ge=-90, le=90)
    longitude: Optional[float] = Field(default=None, ge=-180, le=180)
    start_time: Optional[time] = None
    end_time: Optional[time] = None
    estimated_cost: Optional[float] = Field(default=None, ge=0)
    currency: Optional[str] = Field(default=None, min_length=3, max_length=3)
    notes: Optional[str] = Field(default=None, max_length=2000)

    @model_validator(mode="after")
    def _times_valid(self) -> "TripItemCreateRequest":
        if self.start_time and self.end_time and self.end_time <= self.start_time:
            raise ValueError("end_time must be after start_time.")
        return self


class TripItemUpdateRequest(BaseModel):
    """All fields optional — only supplied fields are changed."""

    item_type: Optional[TripItemType] = None
    title: Optional[str] = Field(default=None, min_length=1, max_length=255)
    description: Optional[str] = Field(default=None, max_length=2000)
    location_name: Optional[str] = Field(default=None, max_length=255)
    latitude: Optional[float] = Field(default=None, ge=-90, le=90)
    longitude: Optional[float] = Field(default=None, ge=-180, le=180)
    start_time: Optional[time] = None
    end_time: Optional[time] = None
    estimated_cost: Optional[float] = Field(default=None, ge=0)
    currency: Optional[str] = Field(default=None, min_length=3, max_length=3)
    sort_order: Optional[int] = Field(default=None, ge=0)
    notes: Optional[str] = Field(default=None, max_length=2000)


class TripItemResponse(BaseModel):
    id: uuid.UUID
    trip_day_id: uuid.UUID
    item_type: TripItemType
    title: str
    description: Optional[str] = None
    location_name: Optional[str] = None
    latitude: Optional[float] = None
    longitude: Optional[float] = None
    start_time: Optional[time] = None
    end_time: Optional[time] = None
    estimated_cost: Optional[float] = None
    currency: Optional[str] = None
    provider: Optional[str] = None
    # Provenance and presentation fields stored on the item: `source` is one of
    # "provider" | "estimated" | "client_snapshot" | "manual" (None on older rows);
    # `booking_link` and `image_url` are only ever set server-side (never accepted from a
    # client write), so the UI can render them without treating them as user input.
    source: Optional[str] = None
    booking_link: Optional[str] = None
    image_url: Optional[str] = None
    sort_order: int
    notes: Optional[str] = None

    model_config = {"from_attributes": True}


class TripDayResponse(BaseModel):
    id: uuid.UUID
    trip_id: uuid.UUID
    day_number: int
    date: date
    weather_summary: Optional[str] = None
    items: list[TripItemResponse] = Field(default_factory=list)

    model_config = {"from_attributes": True}


class ValidationIssue(BaseModel):
    code: str
    message: str
    item_ids: list[uuid.UUID] = Field(default_factory=list)


class ItineraryValidationResult(BaseModel):
    is_valid: bool
    issues: list[ValidationIssue] = Field(default_factory=list)


class TripVersionResponse(BaseModel):
    id: uuid.UUID
    trip_id: uuid.UUID
    version_number: int
    created_by: uuid.UUID
    change_summary: str
    parent_version_id: Optional[uuid.UUID] = None

    model_config = {"from_attributes": True}
