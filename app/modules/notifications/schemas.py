"""Pydantic schemas for notification endpoints (Master Blueprint §46-47)."""
from __future__ import annotations

import uuid
from datetime import datetime
from typing import Optional

from pydantic import BaseModel

from app.core.constants import NotificationType


class NotificationResponse(BaseModel):
    id: uuid.UUID
    notification_type: NotificationType
    title: str
    body: str
    link: Optional[str] = None
    read_at: Optional[datetime] = None
    created_at: datetime

    model_config = {"from_attributes": True}


class NotificationPreferencesResponse(BaseModel):
    email_enabled: bool
    in_app_enabled: bool

    model_config = {"from_attributes": True}


class NotificationPreferencesUpdateRequest(BaseModel):
    email_enabled: bool
    in_app_enabled: bool
