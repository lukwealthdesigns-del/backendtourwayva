"""
LifecycleService — trial and subscription notifications and expiry.

  * a trial about to end   -> ONE reminder (TRIAL_REMINDER_DAYS before)
  * a trial that ended     -> ONE notice
  * a subscription that will simply end (no auto-renew, not cancelled by the user) -> ONE reminder
  * a subscription past its period end -> status EXPIRED + ONE notice. Auto-renewing
    subscriptions get SUBSCRIPTION_GRACE_HOURS first (a late renewal webhook must not
    flip a paying customer to expired). Access itself never depends on this sweep:
    `get_active_subscription` already honours the period end (and grace).

Each notice is sent at most once (the marker is set in the same transaction as the
notification, and rows are fetched with SKIP LOCKED), and a run that dies midway simply
continues on the next tick. Messages are written from facts in the database.
"""
from __future__ import annotations

import math
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Optional

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.constants import NotificationType, SubscriptionStatus
from app.core.logging import get_logger
from app.modules.notifications.service import NotificationService
from app.providers.email.factory import direct_email_provider
from app.repositories.lifecycle_repository import LifecycleRepository
from app.repositories.monetization_repository import MonetizationRepository

logger = get_logger(__name__)

BATCH_SIZE = 200


def format_date(value: datetime) -> str:
    return value.astimezone(timezone.utc).strftime("%B %d, %Y").replace(" 0", " ")


def should_expire(*, auto_renew: bool, period_end: datetime, now: datetime, grace: timedelta) -> bool:
    """A subscription is past its end when the period is over — and an auto-renewing one gets `grace`
    first. (The repository query applies the same rule in SQL; this is the in-code safety net.)"""
    return period_end < now - (grace if auto_renew else timedelta(0))


def days_until(target: datetime, now: datetime) -> int:
    return max(1, math.ceil((target - now).total_seconds() / 86400))


class LifecycleService:
    def __init__(
        self,
        db: AsyncSession,
        *,
        notifications: Optional[NotificationService] = None,
        now: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
    ):
        self.db = db
        self.repo = LifecycleRepository(db)
        self.monetization = MonetizationRepository(db)
        # This already runs in a worker: send email directly instead of queueing from the queue.
        self.notifications = notifications or NotificationService(db, email_provider=direct_email_provider(db))
        self._now = now

    async def run(self) -> dict[str, int]:
        counts = {
            "subscriptions_expired": await self.expire_subscriptions(),
            "trial_reminders": await self.remind_trials(),
            "trials_ended": await self.notify_ended_trials(),
            "subscription_reminders": await self.remind_subscriptions(),
        }
        if any(counts.values()):
            logger.info("lifecycle_run", **counts)
        return counts

    async def _notify(self, user_id, *, kind: NotificationType, title: str, body: str) -> None:
        await self.notifications.notify(user_id=user_id, notification_type=kind, title=title, body=body, send_email=True)

    async def remind_trials(self) -> int:
        now = self._now()
        trials = await self.repo.trials_needing_reminder(now, timedelta(days=settings.TRIAL_REMINDER_DAYS), BATCH_SIZE)
        for trial in trials:
            days = days_until(trial.expires_at, now)
            trial.reminder_sent_at = now
            await self._notify(
                trial.user_id, kind=NotificationType.TRIAL_EXPIRING,
                title=f"Your free trial ends in {days} day{'s' if days != 1 else ''}",
                body=f"Your Tour-Wayva trial ends on {format_date(trial.expires_at)}. Choose a plan to keep "
                     "the Companion, hotel and flight search and the rest of your trial features.",
            )
        return len(trials)

    async def notify_ended_trials(self) -> int:
        now = self._now()
        trials = await self.repo.trials_expired_unnotified(now, BATCH_SIZE)
        for trial in trials:
            trial.expired_notified_at = now
            await self._notify(
                trial.user_id, kind=NotificationType.TRIAL_EXPIRING,
                title="Your free trial has ended",
                body="You're now on the free plan (Discover, itinerary planning and weather). "
                     "Choose a plan any time to get the Companion, hotels, flights and more back.",
            )
        return len(trials)

    async def remind_subscriptions(self) -> int:
        now = self._now()
        subscriptions = await self.repo.subscriptions_needing_reminder(
            now, timedelta(days=settings.SUBSCRIPTION_REMINDER_DAYS), BATCH_SIZE
        )
        for subscription in subscriptions:
            plan = await self.monetization.get_plan(subscription.plan_id)
            subscription.reminder_sent_at = now
            await self._notify(
                subscription.user_id, kind=NotificationType.SUBSCRIPTION_EVENT,
                title="Your plan is about to end",
                body=f"Your {plan.name if plan else 'Tour-Wayva'} plan ends on "
                     f"{format_date(subscription.current_period_end)}. Renew before then to keep your features.",
            )
        return len(subscriptions)

    async def expire_subscriptions(self) -> int:
        now = self._now()
        subscriptions = await self.repo.subscriptions_to_expire(
            now, timedelta(hours=settings.SUBSCRIPTION_GRACE_HOURS), BATCH_SIZE
        )
        grace = timedelta(hours=settings.SUBSCRIPTION_GRACE_HOURS)
        subscriptions = [
            s for s in subscriptions
            if should_expire(auto_renew=s.auto_renew, period_end=s.current_period_end, now=now, grace=grace)
        ]
        for subscription in subscriptions:
            plan = await self.monetization.get_plan(subscription.plan_id)
            subscription.status = SubscriptionStatus.EXPIRED
            subscription.expired_notified_at = now
            await self._notify(
                subscription.user_id, kind=NotificationType.SUBSCRIPTION_EVENT,
                title="Your plan has ended",
                body=f"Your {plan.name if plan else 'Tour-Wayva'} plan ended on "
                     f"{format_date(subscription.current_period_end)}, so your account is back on the free plan. "
                     "You can subscribe again at any time.",
            )
        return len(subscriptions)
