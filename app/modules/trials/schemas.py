"""Pydantic schemas for trial endpoints (Master Blueprint §49)."""
from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, Field

from app.core.constants import FeatureFlag


class TrialConfigResponse(BaseModel):
    is_enabled: bool
    duration_days: int
    included_feature_flags: list[str]


class TrialConfigUpdateRequest(BaseModel):
    """Temporarily open to any authenticated user — will become
    admin-gated once Phase 7 (Admin/RBAC) exists."""

    is_enabled: bool
    duration_days: int = Field(..., ge=1, le=365)
    included_feature_flags: list[FeatureFlag] = Field(default_factory=list)


class UserTrialResponse(BaseModel):
    id: uuid.UUID
    started_at: datetime
    expires_at: datetime
    included_feature_flags: list[str]
    is_active: bool

    model_config = {"from_attributes": True}
