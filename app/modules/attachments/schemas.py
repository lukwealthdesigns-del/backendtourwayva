"""Pydantic schemas for attachment endpoints (Master Blueprint §42)."""
from __future__ import annotations

import uuid
from datetime import datetime
from typing import Optional

from pydantic import BaseModel

from app.core.constants import AttachmentCategory, AttachmentExtractionStatus


class AttachmentResponse(BaseModel):
    id: uuid.UUID
    trip_id: Optional[uuid.UUID] = None
    category: AttachmentCategory
    original_filename: str
    content_type: str
    url: str
    extraction_status: AttachmentExtractionStatus
    extracted_text: Optional[str] = None
    structured_fields: Optional[dict] = None
    user_confirmed: bool
    created_at: datetime

    model_config = {"from_attributes": True}


class AttachmentLinkTripRequest(BaseModel):
    trip_id: uuid.UUID
