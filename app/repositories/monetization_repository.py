"""Repository for plans, subscriptions, trial config/state, and
per-user feature flag overrides."""
from __future__ import annotations

import uuid
from typing import Optional, Sequence

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models.monetization import (
    FeatureFlagOverride,
    FeatureFlagSetting,
    Plan,
    Subscription,
    TrialConfig,
    UserTrial,
)


class MonetizationRepository:
    def __init__(self, db: AsyncSession):
        self.db = db

    # --- Plans ---
    async def create_plan(self, plan: Plan) -> Plan:
        self.db.add(plan)
        await self.db.flush()
        return plan

    async def get_plan(self, plan_id: uuid.UUID) -> Optional[Plan]:
        result = await self.db.execute(select(Plan).where(Plan.id == plan_id))
        return result.scalar_one_or_none()

    async def get_plan_by_slug(self, slug: str) -> Optional[Plan]:
        result = await self.db.execute(select(Plan).where(Plan.slug == slug))
        return result.scalar_one_or_none()

    async def get_plan_by_paystack_code(self, plan_code: str) -> Optional[Plan]:
        result = await self.db.execute(select(Plan).where(Plan.paystack_plan_code == plan_code))
        return result.scalar_one_or_none()

    async def list_active_plans(self) -> Sequence[Plan]:
        result = await self.db.execute(select(Plan).where(Plan.is_active.is_(True)))
        return result.scalars().all()

    # --- Subscriptions ---
    async def create_subscription(self, subscription: Subscription) -> Subscription:
        self.db.add(subscription)
        await self.db.flush()
        return subscription

    async def get_active_subscription(self, user_id: uuid.UUID) -> Optional[Subscription]:
        """The user's CURRENT subscription: status ACTIVE *and* not past its
        period end. (The period-end check matters: nothing flips a lapsed
        subscription's status, so without it a one-month plan would stay
        active forever.)"""
        from datetime import datetime, timedelta, timezone

        from sqlalchemy import and_, or_

        from app.core.config import settings
        from app.core.constants import SubscriptionStatus

        now = datetime.now(timezone.utc)
        grace = timedelta(hours=settings.SUBSCRIPTION_GRACE_HOURS)
        result = await self.db.execute(
            select(Subscription)
            .where(
                Subscription.user_id == user_id,
                Subscription.status == SubscriptionStatus.ACTIVE,
                or_(
                    Subscription.current_period_end > now,
                    # Auto-renewing subscriptions keep access through a short grace window so a
                    # renewal webhook that is a little late never locks a paying customer out.
                    and_(Subscription.auto_renew.is_(True), Subscription.current_period_end > now - grace),
                ),
            )
            .order_by(Subscription.created_at.desc())
        )
        return result.scalars().first()

    async def list_subscriptions_for_user(self, user_id: uuid.UUID) -> Sequence[Subscription]:
        result = await self.db.execute(
            select(Subscription).where(Subscription.user_id == user_id).order_by(Subscription.created_at.desc())
        )
        return result.scalars().all()

    async def save_subscription(self, subscription: Subscription) -> Subscription:
        await self.db.flush()
        return subscription

    # --- Trial config (effectively a singleton) ---
    async def get_trial_config(self) -> Optional[TrialConfig]:
        result = await self.db.execute(select(TrialConfig).order_by(TrialConfig.updated_at.desc()).limit(1))
        return result.scalars().first()

    async def create_trial_config(self, config: TrialConfig) -> TrialConfig:
        self.db.add(config)
        await self.db.flush()
        return config

    async def save_trial_config(self, config: TrialConfig) -> TrialConfig:
        await self.db.flush()
        return config

    # --- User trials ---
    async def get_user_trial(self, user_id: uuid.UUID) -> Optional[UserTrial]:
        result = await self.db.execute(select(UserTrial).where(UserTrial.user_id == user_id))
        return result.scalar_one_or_none()

    async def create_user_trial(self, trial: UserTrial) -> UserTrial:
        self.db.add(trial)
        await self.db.flush()
        return trial

    # --- Global feature flag settings (admin kill switch / open to all) ---
    async def list_flag_settings(self) -> Sequence[FeatureFlagSetting]:
        result = await self.db.execute(select(FeatureFlagSetting))
        return result.scalars().all()

    async def get_flag_setting(self, flag: str) -> Optional[FeatureFlagSetting]:
        result = await self.db.execute(select(FeatureFlagSetting).where(FeatureFlagSetting.flag == flag))
        return result.scalar_one_or_none()

    async def create_flag_setting(self, setting: FeatureFlagSetting) -> FeatureFlagSetting:
        self.db.add(setting)
        await self.db.flush()
        return setting

    async def save_flag_setting(self, setting: FeatureFlagSetting) -> FeatureFlagSetting:
        await self.db.flush()
        return setting

    # --- Feature flag overrides ---
    async def list_overrides_for_user(self, user_id: uuid.UUID) -> Sequence[FeatureFlagOverride]:
        result = await self.db.execute(select(FeatureFlagOverride).where(FeatureFlagOverride.user_id == user_id))
        return result.scalars().all()

    async def get_override(self, user_id: uuid.UUID, flag: str) -> Optional[FeatureFlagOverride]:
        result = await self.db.execute(
            select(FeatureFlagOverride).where(
                FeatureFlagOverride.user_id == user_id, FeatureFlagOverride.flag == flag
            )
        )
        return result.scalar_one_or_none()

    async def create_override(self, override: FeatureFlagOverride) -> FeatureFlagOverride:
        self.db.add(override)
        await self.db.flush()
        return override

    async def save_override(self, override: FeatureFlagOverride) -> FeatureFlagOverride:
        await self.db.flush()
        return override
