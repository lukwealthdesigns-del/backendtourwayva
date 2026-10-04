"""Pydantic schemas for trip notes/costs/routes (Master Blueprint's
trip_notes, trip_costs, trip_routes tables)."""
from __future__ import annotations

import uuid
from datetime import datetime
from typing import Optional

from pydantic import BaseModel, Field

from app.providers.maps.interface import TravelMode


class TripNoteCreateRequest(BaseModel):
    content: str = Field(..., min_length=1, max_length=2000)


class TripNoteResponse(BaseModel):
    id: uuid.UUID
    trip_id: uuid.UUID
    user_id: uuid.UUID
    content: str
    created_at: datetime

    model_config = {"from_attributes": True}


class TripCostCreateRequest(BaseModel):
    category: str = Field(..., min_length=1, max_length=50)
    description: Optional[str] = Field(default=None, max_length=255)
    amount: float = Field(..., gt=0)
    currency: str = Field(..., min_length=3, max_length=3)


class TripCostResponse(BaseModel):
    id: uuid.UUID
    trip_id: uuid.UUID
    added_by: uuid.UUID
    category: str
    description: Optional[str] = None
    amount: float
    currency: str
    created_at: datetime

    model_config = {"from_attributes": True}


class TripCostSummaryResponse(BaseModel):
    """Totals grouped by currency — never silently summed across
    mismatched currencies, since that would misrepresent the actual
    total. budget_amount/budget_currency echo the trip's planning
    figure (Trip model) for comparison, when set."""

    totals_by_currency: dict[str, float]
    budget_amount: Optional[float] = None
    budget_currency: Optional[str] = None


class TripRouteRequest(BaseModel):
    origin_item_id: uuid.UUID
    destination_item_id: uuid.UUID
    mode: TravelMode = TravelMode.WALKING


class TripRouteResponse(BaseModel):
    trip_id: uuid.UUID
    origin_item_id: uuid.UUID
    destination_item_id: uuid.UUID
    mode: TravelMode
    distance_meters: float
    duration_seconds: float
    source: str  # "cache" | "live"

    model_config = {"from_attributes": True}
