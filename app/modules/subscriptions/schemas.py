"""Pydantic schemas for plan/subscription endpoints (Master Blueprint §48)."""
from __future__ import annotations

import uuid
from datetime import datetime
from typing import Optional

from pydantic import BaseModel, Field

from app.core.constants import BillingInterval, FeatureFlag, SubscriptionStatus


class PlanCreateRequest(BaseModel):
    """Admin-only (`plans:manage`)."""

    name: str = Field(..., min_length=1, max_length=100)
    slug: str = Field(..., min_length=1, max_length=100)
    price_amount: float = Field(default=0.0, ge=0)
    price_currency: str = Field(default="USD", min_length=3, max_length=3)
    billing_interval: BillingInterval = BillingInterval.FREE
    included_feature_flags: list[FeatureFlag] = Field(default_factory=list)
    # Paystack plan code (PLN_...) for auto-renewing billing. Must match this
    # plan's amount, currency and interval on the Paystack dashboard.
    paystack_plan_code: Optional[str] = Field(default=None, min_length=1, max_length=64)


class PlanUpdateRequest(BaseModel):
    """Only these fields can change after creation; price, currency and
    interval are immutable so existing subscribers are never re-priced."""

    model_config = {"extra": "forbid"}

    paystack_plan_code: Optional[str] = Field(default=None, min_length=1, max_length=64)
    is_active: Optional[bool] = None


class PlanResponse(BaseModel):
    id: uuid.UUID
    name: str
    slug: str
    price_amount: float
    price_currency: str
    billing_interval: BillingInterval
    included_feature_flags: list[str]
    is_active: bool

    model_config = {"from_attributes": True}


class SubscribeRequest(BaseModel):
    plan_id: uuid.UUID


class SubscriptionResponse(BaseModel):
    id: uuid.UUID
    plan_id: uuid.UUID
    status: SubscriptionStatus
    current_period_start: datetime
    current_period_end: datetime
    cancelled_at: Optional[datetime] = None
    auto_renew: bool = False

    model_config = {"from_attributes": True}
