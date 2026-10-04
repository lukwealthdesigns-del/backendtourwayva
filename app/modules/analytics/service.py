"""
AnalyticsService (Master Blueprint §57-58, §89).

`record_ai_usage()` is called right after every LLM call that has a
db session in scope (itinerary generation, Discover, CompanionService).
Cost estimation uses a small static $/1K-token table rather than a live
pricing API — reasonable for order-of-magnitude cost tracking; exact
provider billing should still be the source of truth for actual invoices.

Everything below `record_ai_usage`/`record_event` is read-side reporting
for the admin dashboard — each method independently re-verifies the
caller holds `analytics:view` or `payments:view` (never trusts that an
endpoint already gated the call — Blueprint §1).
"""
from __future__ import annotations

import uuid
from typing import Optional

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.db.models.analytics import AIUsageRecord, AnalyticsEvent, BookingClick
from app.modules.admin.admin_service import AdminService
from app.repositories.analytics_repository import AnalyticsRepository
from app.services.cache_service import ESTIMATED_COST_SAVED_PER_HIT_USD, CacheService

# Static, approximate USD cost per 1K tokens — updated by hand as
# needed, not fetched live. Unknown models fall back to a
# conservative default rather than silently recording $0.
_COST_PER_1K_TOKENS = {
    "gpt-4.1": {"prompt": 0.002, "completion": 0.008},
    "gpt-4.1-mini": {"prompt": 0.0004, "completion": 0.0016},
    "default": {"prompt": 0.001, "completion": 0.003},
}

_COMMISSION_RATE_BY_ITEM_TYPE = {
    "hotel": "ANALYTICS_HOTEL_COMMISSION_RATE",
    "flight": "ANALYTICS_FLIGHT_COMMISSION_RATE",
    "activity": "ANALYTICS_ACTIVITY_COMMISSION_RATE",
}


def _estimate_cost_usd(model: str, prompt_tokens: int, completion_tokens: int) -> float:
    rates = _COST_PER_1K_TOKENS.get(model, _COST_PER_1K_TOKENS["default"])
    return (prompt_tokens / 1000) * rates["prompt"] + (completion_tokens / 1000) * rates["completion"]


def estimate_commission_usd(*, item_type: str, estimated_value_usd: float) -> float:
    """`estimated_value_usd` should already be converted to USD by the
    caller (booking links show prices in the trip's currency); an unknown
    item_type earns no estimated commission rather than a guessed rate."""
    setting_name = _COMMISSION_RATE_BY_ITEM_TYPE.get(item_type)
    if setting_name is None or estimated_value_usd <= 0:
        return 0.0
    rate = getattr(settings, setting_name, 0.0)
    return round(estimated_value_usd * rate, 4)


class AnalyticsService:
    def __init__(self, db: AsyncSession):
        self.db = db
        self.repo = AnalyticsRepository(db)
        self.admin_service = AdminService(db)

    # --- Writes ------------------------------------------------------

    async def record_ai_usage(
        self,
        *,
        user_id: Optional[uuid.UUID],
        feature: str,
        model_used: str,
        prompt_tokens: int,
        completion_tokens: int,
        used_fallback: bool,
        trip_id: Optional[uuid.UUID] = None,
    ) -> AIUsageRecord:
        cost = _estimate_cost_usd(model_used, prompt_tokens, completion_tokens)
        record = AIUsageRecord(
            user_id=user_id, trip_id=trip_id, feature=feature, model_used=model_used,
            prompt_tokens=prompt_tokens, completion_tokens=completion_tokens,
            estimated_cost_usd=cost, used_fallback=used_fallback,
        )
        result = await self.repo.record_ai_usage(record)
        await self.db.commit()
        return result

    async def record_event(self, *, user_id: Optional[uuid.UUID], event_type: str, properties: Optional[dict] = None) -> AnalyticsEvent:
        event = AnalyticsEvent(user_id=user_id, event_type=event_type, properties=properties)
        result = await self.repo.record_event(event)
        await self.db.commit()
        return result

    async def record_booking_click(
        self,
        *,
        user_id: Optional[uuid.UUID],
        trip_id: Optional[uuid.UUID],
        trip_item_id: Optional[uuid.UUID],
        item_type: str,
        provider: str,
        estimated_value: float,
        estimated_value_currency: str,
        estimated_value_usd: float,
    ) -> BookingClick:
        """Called from the "I'm about to book this" click-through endpoint
        (never on itinerary generation itself — only an explicit click
        signals booking intent worth counting)."""
        click = BookingClick(
            user_id=user_id, trip_id=trip_id, trip_item_id=trip_item_id,
            item_type=item_type, provider=provider,
            estimated_value=estimated_value, estimated_value_currency=estimated_value_currency.upper(),
            estimated_commission_usd=estimate_commission_usd(item_type=item_type, estimated_value_usd=estimated_value_usd),
        )
        result = await self.repo.record_booking_click(click)
        await self.db.commit()
        return result

    # --- Reads (all admin-gated) --------------------------------------

    async def get_ai_cost_summary(self, *, actor_id: uuid.UUID) -> dict:
        await self.admin_service.require_permission(user_id=actor_id, permission="analytics:view")
        return {
            "total_cost_usd": await self.repo.total_ai_cost(),
            "total_requests": await self.repo.total_ai_requests(),
        }

    async def get_ai_cost_by_user(self, *, actor_id: uuid.UUID, target_user_id: uuid.UUID) -> dict:
        await self.admin_service.require_permission(user_id=actor_id, permission="analytics:view")
        result = await self.repo.ai_cost_by_user(target_user_id)
        result["user_id"] = str(target_user_id)
        return result

    async def get_ai_cost_by_trip(self, *, actor_id: uuid.UUID, trip_id: uuid.UUID) -> dict:
        await self.admin_service.require_permission(user_id=actor_id, permission="analytics:view")
        return await self.repo.ai_cost_by_trip(trip_id)

    async def get_cache_stats(self, *, actor_id: uuid.UUID) -> dict:
        """Blueprint §58/§89 "cache hit rate" and "cost savings" — per
        provider-category hit rate plus an order-of-magnitude estimated
        dollar saving from avoided provider calls."""
        await self.admin_service.require_permission(user_id=actor_id, permission="analytics:view")
        raw = await CacheService.cache_hit_rate_stats()
        by_category = []
        total_hits = 0
        total_estimated_savings = 0.0
        for category, counts in raw.items():
            hits, misses = counts["hits"], counts["misses"]
            total = hits + misses
            per_hit = ESTIMATED_COST_SAVED_PER_HIT_USD.get(category, 0.0)
            savings = round(hits * per_hit, 2)
            total_hits += hits
            total_estimated_savings += savings
            by_category.append({
                "category": category, "hits": hits, "misses": misses,
                "hit_rate": round(hits / total, 4) if total else None,
                "estimated_savings_usd": savings,
            })
        return {
            "by_category": sorted(by_category, key=lambda c: c["category"]),
            "total_hits": total_hits,
            "total_estimated_savings_usd": round(total_estimated_savings, 2),
        }

    async def reset_cache_stats(self, *, actor_id: uuid.UUID) -> None:
        await self.admin_service.require_permission(user_id=actor_id, permission="analytics:view")
        await CacheService.reset_cache_hit_rate_stats()

    async def get_revenue_dashboard(self, *, actor_id: uuid.UUID, window_days: int) -> dict:
        """Blueprint §57 revenue block in one call: MRR, churn, trial
        conversion, gross revenue, and estimated affiliate revenue."""
        await self.admin_service.require_permission(user_id=actor_id, permission="payments:view")
        return {
            "mrr_by_currency": await self.repo.mrr_by_currency(),
            "churn": await self.repo.churn_rate(window_days=window_days),
            "trial_conversion": await self.repo.trial_conversion_rate(window_days=window_days),
            "revenue": await self.repo.revenue_summary(window_days=window_days),
            "affiliate": await self.repo.affiliate_revenue_summary(window_days=window_days),
        }
