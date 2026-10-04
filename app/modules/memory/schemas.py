"""Pydantic schemas for user-memory endpoints (Master Blueprint §37)."""
from __future__ import annotations

import uuid
from datetime import datetime
from typing import Optional

from pydantic import BaseModel, Field

from app.core.constants import MemorySource


class MemoryCreateRequest(BaseModel):
    content: str = Field(..., min_length=1, max_length=1000)


class MemoryUpdateRequest(BaseModel):
    content: Optional[str] = Field(default=None, min_length=1, max_length=1000)
    is_enabled: Optional[bool] = None


class MemoryResponse(BaseModel):
    id: uuid.UUID
    content: str
    source: MemorySource
    confidence: Optional[float] = None
    is_enabled: bool
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}
