"""Pydantic schemas for Companion endpoints (Master Blueprint §35-36)."""
from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any, Optional

from pydantic import BaseModel, Field

from app.core.constants import MessageRole


class ConversationCreateRequest(BaseModel):
    title: Optional[str] = Field(default=None, max_length=255)
    trip_id: Optional[uuid.UUID] = None


class ConversationResponse(BaseModel):
    id: uuid.UUID
    trip_id: Optional[uuid.UUID] = None
    title: Optional[str] = None
    created_at: datetime

    model_config = {"from_attributes": True}


class ClientContext(BaseModel):
    """What the app already knows about where the user is, sent with a message so the Companion can answer "near me". Never stored."""
    latitude: Optional[float] = Field(default=None, ge=-90, le=90)
    longitude: Optional[float] = Field(default=None, ge=-180, le=180)
    place: Optional[str] = Field(default=None, max_length=120)
    source: Optional[str] = Field(default=None, pattern="^(chosen|approximate|device)$")
    timezone: Optional[str] = Field(default=None, max_length=64)


class MessageCreateRequest(BaseModel):
    content: str = Field(..., min_length=1, max_length=4000)
    client_context: Optional[ClientContext] = None


class MessageResponse(BaseModel):
    id: uuid.UUID
    role: MessageRole
    content: str
    model_used: Optional[str] = None
    created_at: datetime
    meta: Optional[dict[str, Any]] = None     # {"trip_ids": [...], "images": [{url, thumbnail_url, alt, ...}]}

    # `model_used` starts with pydantic's reserved "model_" prefix; opt out of the warning.
    model_config = {"from_attributes": True, "protected_namespaces": ()}


class VoiceMessageResponse(BaseModel):
    transcript: str
    reply: MessageResponse


class PendingChangeResponse(BaseModel):
    id: uuid.UUID
    conversation_id: uuid.UUID
    trip_id: uuid.UUID
    action: str
    summary: str
    status: str
    created_at: datetime

    model_config = {"from_attributes": True}


class ConfirmChangeResult(BaseModel):
    change: PendingChangeResponse
    applied: dict
