"""Database side of the planning limits: read/update the admin policy, resolve one user's effective limits, count the
user's generations this month, and sum what a trip has cost in AI spend. The rules themselves are in policy_rules.py."""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.constants import FeatureFlag
from app.db.models.analytics import AIUsageRecord, AnalyticsEvent
from app.db.models.planning_policy import PlanningPolicy
from app.modules.entitlements.service import EntitlementService
from app.modules.planning.policy_rules import EffectiveLimits, PolicyConfig, effective_limits

GENERATION_EVENT = "itinerary_generation_completed"


def _month_start(now: Optional[datetime] = None) -> datetime:
    now = now or datetime.now(timezone.utc)
    return now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)


def config_from_row(row: PlanningPolicy) -> PolicyConfig:
    return PolicyConfig(
        growth_mode=row.growth_mode, growth_max_days=row.growth_max_days, premium_max_days=row.premium_max_days,
        free_max_days=row.free_max_days, full_detail_max_days=row.full_detail_max_days, chunk_days=row.chunk_days,
        growth_monthly_generations=row.growth_monthly_generations, premium_monthly_generations=row.premium_monthly_generations,
        free_monthly_generations=row.free_monthly_generations, cost_alert_usd=row.cost_alert_usd,
    )


class PlanningPolicyService:
    def __init__(self, db: AsyncSession) -> None:
        self.db = db

    async def get_row(self) -> PlanningPolicy:
        result = await self.db.execute(select(PlanningPolicy).order_by(PlanningPolicy.updated_at.desc()).limit(1))
        row = result.scalar_one_or_none()
        if row is None:                       # first read on a database that skipped the seed row
            row = PlanningPolicy()
            self.db.add(row)
            await self.db.flush()
            await self.db.refresh(row)
        return row

    async def get_config(self) -> PolicyConfig:
        return config_from_row(await self.get_row())

    async def update(self, updates: dict) -> PlanningPolicy:
        row = await self.get_row()
        for key, value in updates.items():
            if value is not None and hasattr(row, key):
                setattr(row, key, value)
        # Keep the settings consistent: nothing may promise less than full-detail trips, and premium >= free.
        row.growth_max_days = max(row.growth_max_days, row.full_detail_max_days)
        row.premium_max_days = max(row.premium_max_days, row.full_detail_max_days)
        row.free_max_days = max(row.free_max_days, row.full_detail_max_days)
        await self.db.commit()
        await self.db.refresh(row)
        return row

    async def limits_for(self, user_id: uuid.UUID) -> EffectiveLimits:
        config = await self.get_config()
        is_premium = False if config.growth_mode else await EntitlementService(self.db).has_feature(user_id, FeatureFlag.PREMIUM_AI)
        return effective_limits(config, is_premium=is_premium)

    async def generations_this_month(self, user_id: uuid.UUID) -> int:
        result = await self.db.execute(
            select(func.count()).select_from(AnalyticsEvent).where(
                AnalyticsEvent.user_id == user_id, AnalyticsEvent.event_type == GENERATION_EVENT,
                AnalyticsEvent.created_at >= _month_start(),
            )
        )
        return int(result.scalar_one() or 0)

    async def trip_ai_cost_usd(self, trip_id: uuid.UUID) -> float:
        result = await self.db.execute(
            select(func.coalesce(func.sum(AIUsageRecord.estimated_cost_usd), 0.0)).where(
                AIUsageRecord.trip_id == trip_id, AIUsageRecord.feature.in_(("itinerary_generation", "itinerary_outline"))
            )
        )
        return float(result.scalar_one() or 0.0)
