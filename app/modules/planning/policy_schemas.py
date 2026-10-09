from __future__ import annotations

from typing import Optional

from pydantic import BaseModel, Field

from app.modules.planning.policy_rules import (
    HARD_FULL_DETAIL_MAX_DAYS, HARD_MAX_DAYS, MAX_CHUNK_DAYS, MIN_CHUNK_DAYS,
)


class PlanningPolicyConfigResponse(BaseModel):
    """What the admin edits."""

    model_config = {"from_attributes": True}

    growth_mode: bool
    growth_max_days: int
    premium_max_days: int
    free_max_days: int
    full_detail_max_days: int
    chunk_days: int
    growth_monthly_generations: int
    premium_monthly_generations: int
    free_monthly_generations: int
    cost_alert_usd: float


class PlanningPolicyUpdate(BaseModel):
    """Partial update; every field optional, every number range-checked."""

    growth_mode: Optional[bool] = None
    growth_max_days: Optional[int] = Field(default=None, ge=1, le=HARD_MAX_DAYS)
    premium_max_days: Optional[int] = Field(default=None, ge=1, le=HARD_MAX_DAYS)
    free_max_days: Optional[int] = Field(default=None, ge=1, le=HARD_MAX_DAYS)
    full_detail_max_days: Optional[int] = Field(default=None, ge=1, le=HARD_FULL_DETAIL_MAX_DAYS)
    chunk_days: Optional[int] = Field(default=None, ge=MIN_CHUNK_DAYS, le=MAX_CHUNK_DAYS)
    growth_monthly_generations: Optional[int] = Field(default=None, ge=0, le=100000)
    premium_monthly_generations: Optional[int] = Field(default=None, ge=0, le=100000)
    free_monthly_generations: Optional[int] = Field(default=None, ge=0, le=100000)
    cost_alert_usd: Optional[float] = Field(default=None, ge=0, le=1000)


class EffectivePlanningLimits(BaseModel):
    """What the signed-in user can do right now; drives the trip wizard and the long-trip explanation."""

    tier: str                       # "growth" | "premium" | "free"
    growth: bool                    # True while the admin's growth mode is on (shown as "included during our growth period")
    max_days: int
    full_detail_max_days: int
    chunk_days: int
    monthly_limit: int
    used_this_month: int
    remaining_this_month: int
