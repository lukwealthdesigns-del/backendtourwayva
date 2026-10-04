"""Repository for AI usage records, generic analytics events, and the
revenue/cost-dashboard aggregate queries behind GET /admin/analytics/*
(Master Blueprint §57-58, §89).

Revenue figures here read from `payments` (money that was actually
CHARGED and verified with Paystack — see app/modules/payments), not from
`subscriptions.status`, because a subscription can be ACTIVE without a
successful payment (e.g. a free plan, or an admin-granted trial). MRR is
the one exception: it is necessarily forward-looking (a snapshot of what
active recurring subscriptions are WORTH per month), so it reads
`subscriptions` + `plans` directly.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from typing import Optional, Sequence

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.constants import BillingInterval, PaymentStatus, SubscriptionStatus
from app.db.models.analytics import AIUsageRecord, AnalyticsEvent, BookingClick
from app.db.models.monetization import Plan, Subscription, UserTrial
from app.db.models.payment import Payment


class AnalyticsRepository:
    def __init__(self, db: AsyncSession):
        self.db = db

    # --- AI usage --------------------------------------------------------

    async def record_ai_usage(self, record: AIUsageRecord) -> AIUsageRecord:
        self.db.add(record)
        await self.db.flush()
        return record

    async def total_ai_cost(self, *, user_id: Optional[uuid.UUID] = None) -> float:
        stmt = select(func.coalesce(func.sum(AIUsageRecord.estimated_cost_usd), 0.0))
        if user_id is not None:
            stmt = stmt.where(AIUsageRecord.user_id == user_id)
        result = await self.db.execute(stmt)
        return float(result.scalar_one())

    async def total_ai_requests(self, *, user_id: Optional[uuid.UUID] = None) -> int:
        stmt = select(func.count()).select_from(AIUsageRecord)
        if user_id is not None:
            stmt = stmt.where(AIUsageRecord.user_id == user_id)
        result = await self.db.execute(stmt)
        return result.scalar_one()

    async def ai_cost_by_user(self, user_id: uuid.UUID) -> dict:
        """Blueprint §58 "cost/user" — total spend and request count for
        ONE user, plus a per-feature breakdown (itinerary_generation,
        discover, companion_* ...) so an admin can see what's driving it."""
        totals = await self.db.execute(
            select(
                func.coalesce(func.sum(AIUsageRecord.estimated_cost_usd), 0.0),
                func.count(),
                func.coalesce(func.sum(AIUsageRecord.prompt_tokens + AIUsageRecord.completion_tokens), 0),
            ).where(AIUsageRecord.user_id == user_id)
        )
        total_cost, total_requests, total_tokens = totals.one()

        by_feature = await self.db.execute(
            select(
                AIUsageRecord.feature,
                func.coalesce(func.sum(AIUsageRecord.estimated_cost_usd), 0.0),
                func.count(),
            )
            .where(AIUsageRecord.user_id == user_id)
            .group_by(AIUsageRecord.feature)
            .order_by(func.sum(AIUsageRecord.estimated_cost_usd).desc())
        )
        return {
            "total_cost_usd": float(total_cost),
            "total_requests": int(total_requests),
            "total_tokens": int(total_tokens),
            "by_feature": [
                {"feature": feature, "cost_usd": float(cost), "requests": int(count)}
                for feature, cost, count in by_feature.all()
            ],
        }

    async def ai_cost_by_trip(self, trip_id: uuid.UUID) -> dict:
        """Blueprint §58 "cost/trip" — total AI spend attributable to ONE
        trip. Only itinerary_generation and trip-scoped Companion turns
        carry a trip_id (Discover searches happen before a trip exists, so
        they are never included here by design)."""
        totals = await self.db.execute(
            select(
                func.coalesce(func.sum(AIUsageRecord.estimated_cost_usd), 0.0),
                func.count(),
            ).where(AIUsageRecord.trip_id == trip_id)
        )
        total_cost, total_requests = totals.one()

        by_feature = await self.db.execute(
            select(
                AIUsageRecord.feature,
                func.coalesce(func.sum(AIUsageRecord.estimated_cost_usd), 0.0),
                func.count(),
            )
            .where(AIUsageRecord.trip_id == trip_id)
            .group_by(AIUsageRecord.feature)
            .order_by(func.sum(AIUsageRecord.estimated_cost_usd).desc())
        )
        return {
            "trip_id": str(trip_id),
            "total_cost_usd": float(total_cost),
            "total_requests": int(total_requests),
            "by_feature": [
                {"feature": feature, "cost_usd": float(cost), "requests": int(count)}
                for feature, cost, count in by_feature.all()
            ],
        }

    # --- Generic events ----------------------------------------------------

    async def record_event(self, event: AnalyticsEvent) -> AnalyticsEvent:
        self.db.add(event)
        await self.db.flush()
        return event

    async def count_events(self, event_type: str) -> int:
        result = await self.db.execute(
            select(func.count()).select_from(AnalyticsEvent).where(AnalyticsEvent.event_type == event_type)
        )
        return result.scalar_one()

    async def list_recent_events(self, *, event_type: Optional[str] = None, limit: int = 50) -> Sequence[AnalyticsEvent]:
        stmt = select(AnalyticsEvent)
        if event_type:
            stmt = stmt.where(AnalyticsEvent.event_type == event_type)
        stmt = stmt.order_by(AnalyticsEvent.created_at.desc()).limit(limit)
        result = await self.db.execute(stmt)
        return result.scalars().all()

    # --- Revenue / MRR / churn / trial conversion (Blueprint §57, §89) ---

    async def mrr_by_currency(self) -> dict[str, float]:
        """Monthly Recurring Revenue, grouped by plan currency (never
        currency-converted across plans — mixing currencies into one number
        would require picking a live exchange rate for a snapshot metric,
        which is exactly the kind of implicit assumption Blueprint §108
        warns against; an admin dashboard can sum these itself once it
        knows which currencies it cares about).

        Only ACTIVE subscriptions still within their current period count.
        YEARLY plans are divided by 12; FREE plans contribute 0."""
        now = datetime.now(timezone.utc)
        rows = await self.db.execute(
            select(Plan.price_currency, Plan.price_amount, Plan.billing_interval)
            .join(Subscription, Subscription.plan_id == Plan.id)
            .where(Subscription.status == SubscriptionStatus.ACTIVE, Subscription.current_period_end > now)
        )
        mrr: dict[str, float] = {}
        for currency, amount, interval in rows.all():
            monthly = 0.0
            if interval == BillingInterval.MONTHLY:
                monthly = amount
            elif interval == BillingInterval.YEARLY:
                monthly = amount / 12.0
            # FREE contributes 0 explicitly (not skipped) so a currency with
            # only free subscribers still shows up at 0 rather than being absent.
            mrr[currency] = round(mrr.get(currency, 0.0) + monthly, 2)
        return mrr

    async def churn_rate(self, *, window_days: int) -> dict:
        """Simple period churn: subscriptions cancelled in the trailing
        window, divided by subscriptions that were active at the START of
        that window. Standard SaaS definition — not survivorship-adjusted,
        not MRR-weighted (that would need per-plan revenue attribution on
        top of this, a reasonable Phase-8+ refinement)."""
        now = datetime.now(timezone.utc)
        window_start = now - timedelta(days=window_days)

        active_at_start = await self.db.execute(
            select(func.count()).select_from(Subscription).where(
                Subscription.status.in_([SubscriptionStatus.ACTIVE, SubscriptionStatus.CANCELLED]),
                Subscription.created_at <= window_start,
                # still "active as of window_start": either never cancelled, or
                # cancelled AFTER window_start (so it was live at that moment)
                (Subscription.cancelled_at.is_(None)) | (Subscription.cancelled_at > window_start),
            )
        )
        active_at_start_count = int(active_at_start.scalar_one())

        cancelled_in_window = await self.db.execute(
            select(func.count()).select_from(Subscription).where(
                Subscription.cancelled_at.is_not(None),
                Subscription.cancelled_at > window_start,
                Subscription.cancelled_at <= now,
            )
        )
        cancelled_count = int(cancelled_in_window.scalar_one())

        rate = (cancelled_count / active_at_start_count) if active_at_start_count > 0 else None
        return {
            "window_days": window_days,
            "active_at_window_start": active_at_start_count,
            "cancelled_in_window": cancelled_count,
            "churn_rate": round(rate, 4) if rate is not None else None,
        }

    async def trial_conversion_rate(self, *, window_days: int) -> dict:
        """Of the trials that EXPIRED in the trailing window, how many
        belong to a user who has since taken out a paid subscription
        (any Subscription row for that user with a non-free plan,
        regardless of current status — converting once counts, even if
        they later cancelled; that's churn, tracked separately)."""
        now = datetime.now(timezone.utc)
        window_start = now - timedelta(days=window_days)

        expired_trials = await self.db.execute(
            select(UserTrial.user_id).where(
                UserTrial.expires_at > window_start, UserTrial.expires_at <= now,
            )
        )
        trial_user_ids = [row[0] for row in expired_trials.all()]
        if not trial_user_ids:
            return {"window_days": window_days, "expired_trials": 0, "converted": 0, "conversion_rate": None}

        converted = await self.db.execute(
            select(func.count(func.distinct(Subscription.user_id)))
            .join(Plan, Plan.id == Subscription.plan_id)
            .where(Subscription.user_id.in_(trial_user_ids), Plan.billing_interval != BillingInterval.FREE)
        )
        converted_count = int(converted.scalar_one())
        total = len(trial_user_ids)
        return {
            "window_days": window_days,
            "expired_trials": total,
            "converted": converted_count,
            "conversion_rate": round(converted_count / total, 4) if total else None,
        }

    async def revenue_summary(self, *, window_days: int) -> dict:
        """Revenue from VERIFIED successful payments, by currency, in the
        trailing window, NET of refunds: fully refunded and disputed payments
        (status REFUNDED/DISPUTED) drop out entirely, and a partially refunded
        one counts only what was kept. Attributed to the payment's original
        paid_at date, not the refund date. Reads `payments` (real charges),
        never `subscriptions` (state, not money) — Blueprint §108."""
        now = datetime.now(timezone.utc)
        window_start = now - timedelta(days=window_days)
        rows = await self.db.execute(
            select(
                Payment.currency,
                func.coalesce(func.sum(Payment.amount_minor - Payment.refunded_amount_minor), 0),
                func.count(),
            )
            .where(
                Payment.status == PaymentStatus.SUCCESS,
                Payment.paid_at.is_not(None),
                Payment.paid_at > window_start,
                Payment.paid_at <= now,
            )
            .group_by(Payment.currency)
        )
        by_currency = []
        for currency, minor_total, count in rows.all():
            by_currency.append({
                "currency": currency,
                "amount": round(int(minor_total) / 100, 2),   # minor unit -> major, per app/utils/money.py convention
                "payment_count": int(count),
            })
        renewals = await self.db.execute(
            select(func.count()).select_from(Payment).where(
                Payment.status == PaymentStatus.SUCCESS, Payment.is_renewal.is_(True),
                Payment.paid_at.is_not(None), Payment.paid_at > window_start, Payment.paid_at <= now,
            )
        )
        return {
            "window_days": window_days,
            "by_currency": by_currency,
            "renewal_payment_count": int(renewals.scalar_one()),
        }

    # --- Affiliate / booking-click revenue (Blueprint §57 "affiliate activity") ---

    async def record_booking_click(self, click: BookingClick) -> BookingClick:
        self.db.add(click)
        await self.db.flush()
        return click

    async def affiliate_revenue_summary(self, *, window_days: int) -> dict:
        """ESTIMATED affiliate revenue from booking-link click-throughs in
        the trailing window — see BookingClick's docstring for why this can
        never be more than an estimate (Tour-Wayva has no real booking/
        payout relationship with Amadeus to confirm an actual commission)."""
        now = datetime.now(timezone.utc)
        window_start = now - timedelta(days=window_days)
        rows = await self.db.execute(
            select(
                BookingClick.item_type,
                func.count(),
                func.coalesce(func.sum(BookingClick.estimated_commission_usd), 0.0),
            )
            .where(BookingClick.created_at > window_start, BookingClick.created_at <= now)
            .group_by(BookingClick.item_type)
        )
        by_type = []
        total = 0.0
        for item_type, count, commission in rows.all():
            by_type.append({"item_type": item_type, "clicks": int(count), "estimated_commission_usd": round(float(commission), 2)})
            total += float(commission)
        return {
            "window_days": window_days,
            "is_estimate": True,
            "total_estimated_commission_usd": round(total, 2),
            "by_item_type": by_type,
        }
