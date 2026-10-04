"""Pydantic schemas for travel history endpoints (Master Blueprint §45)."""
from __future__ import annotations

import uuid
from datetime import date
from typing import Optional

from pydantic import BaseModel


class VisitedDestinationResponse(BaseModel):
    id: uuid.UUID
    trip_id: Optional[uuid.UUID] = None
    destination: str
    start_date: date
    end_date: date

    model_config = {"from_attributes": True}


class VisitedPlaceResponse(BaseModel):
    id: uuid.UUID
    trip_id: Optional[uuid.UUID] = None
    place_name: str
    category: Optional[str] = None
    visited_date: Optional[date] = None
    estimated_cost: Optional[float] = None
    currency: Optional[str] = None

    model_config = {"from_attributes": True}
