"""Request/response schemas for itinerary generation and trip preferences."""
from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any, Literal, Optional

from pydantic import BaseModel, Field, field_validator


def _clean_list(values: Optional[list[str]]) -> Optional[list[str]]:
    if values is None:
        return None
    if len(values) > 20:
        raise ValueError("At most 20 items are allowed.")
    cleaned: list[str] = []
    for raw in values:
        item = raw.strip()
        if not item:
            continue
        if len(item) > 80:
            raise ValueError("Each item must be at most 80 characters.")
        if item.lower() not in (c.lower() for c in cleaned):
            cleaned.append(item)
    return cleaned


class TripPreferencesPayload(BaseModel):
    """Planner inputs (Master Prompt §13). Partial: only the fields sent are
    changed; send an empty list / null to clear one."""

    model_config = {"extra": "forbid"}

    travel_style: Optional[str] = Field(default=None, max_length=50)
    interests: Optional[list[str]] = None
    pace: Optional[Literal["relaxed", "balanced", "packed"]] = None
    walking_preference: Optional[Literal["low", "moderate", "high"]] = None
    hotel_preference: Optional[str] = Field(default=None, max_length=50)
    transport_preference: Optional[str] = Field(default=None, max_length=50)
    food_preferences: Optional[list[str]] = None
    must_see: Optional[list[str]] = None
    avoid: Optional[list[str]] = None

    @field_validator("interests", "food_preferences", "must_see", "avoid")
    @classmethod
    def _lists(cls, v: Optional[list[str]]) -> Optional[list[str]]:
        return _clean_list(v)


class TripPreferencesResponse(BaseModel):
    travel_style: Optional[str] = None
    interests: list[str] = Field(default_factory=list)
    pace: Optional[str] = None
    walking_preference: Optional[str] = None
    hotel_preference: Optional[str] = None
    transport_preference: Optional[str] = None
    food_preferences: list[str] = Field(default_factory=list)
    must_see: list[str] = Field(default_factory=list)
    avoid: list[str] = Field(default_factory=list)

    model_config = {"from_attributes": True}


class GenerateItineraryRequest(BaseModel):
    """Everything about the trip itself (dates, destination, travellers,
    budget) comes from the stored trip — never from this request."""

    model_config = {"extra": "forbid"}

    city_code: Optional[str] = Field(
        default=None, pattern=r"^[A-Za-z]{3}$",
        description="IATA city code (e.g. 'PAR') used to search hotels. Without it hotels are skipped.",
    )
    include_hotels: bool = True
    include_activities: bool = True
    preferences: Optional[TripPreferencesPayload] = None
    from_day: Optional[int] = Field(
        default=None, ge=1, le=366,
        description="Long trips only: first day of the part to plan in detail (1, 8, 15 ... with 7-day parts). "
                    "Omit to plan the first part that has no detailed plan yet.",
    )


class JobError(BaseModel):
    code: str
    message: str
    retryable: bool = False


class JobProgress(BaseModel):
    """Where a running generation is. `percent` is the share complete when `stage` began (it only moves when a real
    workflow step finishes); clients may animate smoothly within a stage but must not show it as exact."""

    stage: Literal["locate", "gather", "currency", "draft", "verify", "save", "done"]
    stage_index: int = Field(ge=0)
    stage_count: int = Field(ge=1)
    percent: int = Field(ge=0, le=100)


class GenerationJobResponse(BaseModel):
    job_id: uuid.UUID
    trip_id: uuid.UUID
    status: Literal["queued", "running", "succeeded", "failed", "cancelled"]
    created_at: datetime
    finished_at: Optional[datetime] = None
    result: Optional[dict[str, Any]] = None
    error: Optional[JobError] = None
    progress: Optional[JobProgress] = None
