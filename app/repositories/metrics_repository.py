"""Daily metrics: read the raw tables, write `daily_metrics`."""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.constants import PaymentStatus
from app.db.models.analytics import AIUsageRecord, AnalyticsEvent, DailyMetric
from app.db.models.attachment import Attachment
from app.db.models.discover import DiscoverySearch
from app.db.models.payment import Payment
from app.db.models.security import LoginAttempt
from app.db.models.trip import Trip, TripVersion
from app.db.models.user import User


def day_bounds(day: date) -> tuple[datetime, datetime]:
    start = datetime(day.year, day.month, day.day, tzinfo=timezone.utc)
    return start, start + timedelta(days=1)


class MetricsRepository:
    def __init__(self, db: AsyncSession):
        self.db = db

    async def _count(self, model, *conditions) -> float:
        result = await self.db.execute(select(func.count()).select_from(model).where(*conditions))
        return float(result.scalar_one())

    async def compute_day(self, day: date) -> dict[str, float]:
        start, end = day_bounds(day)
        metrics: dict[str, float] = {
            "users_registered": await self._count(User, User.created_at >= start, User.created_at < end),
            "users_activated": await self._count(User, User.email_verified_at >= start, User.email_verified_at < end),
            "trips_created": await self._count(Trip, Trip.created_at >= start, Trip.created_at < end),
            "itineraries_generated": await self._count(
                TripVersion.id, TripVersion.created_at >= start, TripVersion.created_at < end,
                TripVersion.change_summary.like("AI-generated%")),
            "discover_searches": await self._count(DiscoverySearch, DiscoverySearch.created_at >= start, DiscoverySearch.created_at < end),
            "companion_turns": await self._count(
                AnalyticsEvent.id, AnalyticsEvent.event_type == "companion_turn",
                AnalyticsEvent.created_at >= start, AnalyticsEvent.created_at < end),
            "attachments_uploaded": await self._count(Attachment, Attachment.created_at >= start, Attachment.created_at < end),
            "logins_failed": await self._count(
                LoginAttempt.id, LoginAttempt.success.is_(False), LoginAttempt.created_at >= start, LoginAttempt.created_at < end),
        }

        ai = (await self.db.execute(
            select(
                func.count(), func.coalesce(func.sum(AIUsageRecord.prompt_tokens), 0),
                func.coalesce(func.sum(AIUsageRecord.completion_tokens), 0),
                func.coalesce(func.sum(AIUsageRecord.estimated_cost_usd), 0.0),
            ).where(AIUsageRecord.created_at >= start, AIUsageRecord.created_at < end)
        )).one()
        metrics.update(ai_requests=float(ai[0]), ai_prompt_tokens=float(ai[1]), ai_completion_tokens=float(ai[2]),
                       ai_cost_usd=round(float(ai[3]), 6))

        paid = await self.db.execute(
            select(Payment.currency, func.count(), func.coalesce(func.sum(Payment.amount_minor), 0))
            .where(Payment.status == PaymentStatus.SUCCESS, Payment.paid_at >= start, Payment.paid_at < end)
            .group_by(Payment.currency)
        )
        total_count = 0.0
        for currency, count, minor in paid.all():
            metrics[f"payments_success_amount_minor:{currency}"] = float(minor)
            total_count += float(count)
        metrics["payments_success_count"] = total_count

        # Refunds are attributed to the day the payment's LATEST refund landed
        # (refunded_at), not the original charge's day. Payments store a
        # cumulative refunded total, so a payment refunded in two steps on
        # different days is counted in full on the later day — an approximation
        # that is exact for the common single-refund case.
        refunded = await self.db.execute(
            select(Payment.currency, func.coalesce(func.sum(Payment.refunded_amount_minor), 0))
            .where(Payment.refunded_at.is_not(None), Payment.refunded_at >= start, Payment.refunded_at < end)
            .group_by(Payment.currency)
        )
        for currency, minor in refunded.all():
            metrics[f"payments_refunded_amount_minor:{currency}"] = float(minor)
        return metrics

    async def upsert(self, day: date, values: dict[str, float]) -> None:
        for key, value in values.items():
            stmt = pg_insert(DailyMetric).values(metric_date=day, metric_key=key, value=value)
            await self.db.execute(stmt.on_conflict_do_update(
                constraint="uq_daily_metrics_date_key", set_={"value": value, "updated_at": func.now()}))

    async def list_range(self, start: date, end: date) -> list[DailyMetric]:
        result = await self.db.execute(
            select(DailyMetric).where(DailyMetric.metric_date >= start, DailyMetric.metric_date <= end)
            .order_by(DailyMetric.metric_date, DailyMetric.metric_key)
        )
        return list(result.scalars().all())
