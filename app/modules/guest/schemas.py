from __future__ import annotations

import uuid
from datetime import date, datetime
from typing import Optional

from pydantic import BaseModel, Field


class GuestSessionCreate(BaseModel):
    """Optional hints from the app so a guest sees their own language and currency from the first screen."""

    language: Optional[str] = Field(default=None, max_length=10)
    currency: Optional[str] = Field(default=None, min_length=3, max_length=3)
    country: Optional[str] = Field(default=None, min_length=2, max_length=2)


class GuestPreferencesUpdate(BaseModel):
    """A guest's language, currency and country (all optional; only what is sent changes)."""

    language: Optional[str] = Field(default=None, min_length=2, max_length=10)
    currency: Optional[str] = Field(default=None, min_length=3, max_length=3)
    country: Optional[str] = Field(default=None, min_length=2, max_length=2)


class GuestLimits(BaseModel):
    companion_messages: int
    generations: int
    max_trips: int
    max_trip_days: int
    session_ttl_hours: int


class GuestUsage(BaseModel):
    companion_messages_used: int
    generations_used: int
    trips: int


class GuestSessionResponse(BaseModel):
    token: Optional[str] = None            # only returned when the session is created: it cannot be recovered later
    expires_at: datetime
    limits: GuestLimits
    usage: GuestUsage


class ClaimedTrip(BaseModel):
    id: uuid.UUID
    title: str
    destination: str
    start_date: Optional[date] = None
    end_date: Optional[date] = None


class ClaimResponse(BaseModel):
    already_claimed: bool
    trips: list[ClaimedTrip]
    primary_trip_id: Optional[uuid.UUID] = None   # the most recently worked-on trip: where the app returns the person to
    conversations: int
    searches: int
