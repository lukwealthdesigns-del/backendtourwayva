"""Queries for the scheduled lifecycle jobs. Every fetch takes a row lock with
SKIP LOCKED, so two overlapping runs (or two workers) never process — and never
notify — the same trial or subscription twice."""
from __future__ import annotations

from datetime import datetime, timedelta
from typing import Sequence

from sqlalchemy import and_, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.constants import SubscriptionStatus
from app.db.models.monetization import Subscription, UserTrial


class LifecycleRepository:
    def __init__(self, db: AsyncSession):
        self.db = db

    async def trials_needing_reminder(self, now: datetime, window: timedelta, limit: int) -> Sequence[UserTrial]:
        result = await self.db.execute(
            select(UserTrial)
            .where(UserTrial.reminder_sent_at.is_(None), UserTrial.expires_at > now, UserTrial.expires_at <= now + window)
            .order_by(UserTrial.expires_at).limit(limit).with_for_update(skip_locked=True)
        )
        return result.scalars().all()

    async def trials_expired_unnotified(self, now: datetime, limit: int) -> Sequence[UserTrial]:
        result = await self.db.execute(
            select(UserTrial)
            .where(UserTrial.expired_notified_at.is_(None), UserTrial.expires_at <= now)
            .order_by(UserTrial.expires_at).limit(limit).with_for_update(skip_locked=True)
        )
        return result.scalars().all()

    async def subscriptions_needing_reminder(self, now: datetime, window: timedelta, limit: int) -> Sequence[Subscription]:
        """Subscriptions that will simply END (no auto-renew, not cancelled by the user)."""
        result = await self.db.execute(
            select(Subscription)
            .where(
                Subscription.status == SubscriptionStatus.ACTIVE,
                Subscription.auto_renew.is_(False),
                Subscription.cancelled_at.is_(None),
                Subscription.reminder_sent_at.is_(None),
                Subscription.current_period_end > now,
                Subscription.current_period_end <= now + window,
            )
            .order_by(Subscription.current_period_end).limit(limit).with_for_update(skip_locked=True)
        )
        return result.scalars().all()

    async def subscriptions_to_expire(self, now: datetime, grace: timedelta, limit: int) -> Sequence[Subscription]:
        """ACTIVE subscriptions past their period end. Auto-renewing ones get `grace` first, so a
        renewal charge/webhook that is slightly late does not flip a paying customer to expired."""
        result = await self.db.execute(
            select(Subscription)
            .where(
                Subscription.status == SubscriptionStatus.ACTIVE,
                or_(
                    and_(Subscription.auto_renew.is_(False), Subscription.current_period_end < now),
                    and_(Subscription.auto_renew.is_(True), Subscription.current_period_end < now - grace),
                ),
            )
            .order_by(Subscription.current_period_end).limit(limit).with_for_update(skip_locked=True)
        )
        return result.scalars().all()
