"""Pydantic schemas for place endpoints."""
from __future__ import annotations

import uuid
from datetime import datetime
from typing import Optional

from pydantic import BaseModel, Field

from app.core.constants import PlaceCategory


class PlaceCreateRequest(BaseModel):
    """Temporary open-to-any-authenticated-user creation endpoint —
    will become admin-gated once Phase 7 (Admin/RBAC) exists."""

    name: str = Field(..., min_length=1, max_length=255)
    category: PlaceCategory
    city: Optional[str] = Field(default=None, max_length=200)
    country: Optional[str] = Field(default=None, min_length=2, max_length=2)
    latitude: float = Field(..., ge=-90, le=90)
    longitude: float = Field(..., ge=-180, le=180)
    description: Optional[str] = Field(default=None, max_length=2000)


class PlaceResponse(BaseModel):
    id: uuid.UUID
    name: str
    category: PlaceCategory
    city: Optional[str] = None
    country: Optional[str] = None
    latitude: float
    longitude: float
    description: Optional[str] = None
    source: str
    created_at: datetime

    model_config = {"from_attributes": True}


class PlaceSearchResponse(BaseModel):
    results: list[PlaceResponse]
    count: int
