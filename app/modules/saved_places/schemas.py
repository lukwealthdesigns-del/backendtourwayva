"""Pydantic schemas for saved-places endpoints."""
from __future__ import annotations

import uuid
from datetime import datetime
from typing import Optional

from pydantic import BaseModel, Field

from app.core.constants import PlaceCategory


class SavePlaceRequest(BaseModel):
    model_config = {"extra": "forbid"}

    name: str = Field(..., min_length=1, max_length=255)
    category: PlaceCategory = PlaceCategory.OTHER
    city: Optional[str] = Field(default=None, max_length=200)
    country: Optional[str] = Field(default=None, min_length=2, max_length=2)
    latitude: float = Field(..., ge=-90, le=90)
    longitude: float = Field(..., ge=-180, le=180)
    notes: Optional[str] = Field(default=None, max_length=1000)
    source: str = Field(default="manual", max_length=50)
    external_ref: Optional[str] = Field(default=None, max_length=255)
    trip_id: Optional[uuid.UUID] = None


class SavedPlaceResponse(BaseModel):
    id: uuid.UUID
    name: str
    category: PlaceCategory
    city: Optional[str] = None
    country: Optional[str] = None
    latitude: float
    longitude: float
    notes: Optional[str] = None
    source: str
    external_ref: Optional[str] = None
    trip_id: Optional[uuid.UUID] = None
    created_at: datetime

    model_config = {"from_attributes": True}


class SavedPlaceListResponse(BaseModel):
    results: list[SavedPlaceResponse]
    count: int
