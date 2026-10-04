"""
Analytics models (Master Blueprint §57-58).

AIUsageRecord is written on every LLM call (see
app/providers/llm/openai_provider.py, which accepts an optional
recorder callback rather than importing this module directly, to
keep the provider layer free of a hard DB dependency — see that
file's docstring for the wiring).

AnalyticsEvent is a generic, minimally-structured event log
(event_type + JSON properties) rather than one table per event
category — the pragmatic starting point Blueprint §57's own list of
disparate event categories (registrations, trip generations,
itinerary edits, etc.) suggests consolidating into, until query
patterns on real data show a need to split it out.
"""
from __future__ import annotations

import uuid
from datetime import date
from typing import Optional

from sqlalchemy import Boolean, Date, Float, ForeignKey, Integer, String, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSON, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin, UUIDPKMixin


class AIUsageRecord(UUIDPKMixin, TimestampMixin, Base):
    __tablename__ = "ai_usage_records"

    user_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True
    )
    # Nullable: only itinerary_generation and companion turns tied to a trip
    # conversation have one — Discover searches happen before any trip
    # exists. Lets admin analytics answer "what did THIS trip cost in AI
    # spend" (Blueprint §58's cost/trip) without guessing at a trip for
    # usage that genuinely has none.
    trip_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True), ForeignKey("trips.id", ondelete="SET NULL"), nullable=True, index=True
    )
    feature: Mapped[str] = mapped_column(String(50), nullable=False, index=True)  # e.g. "companion", "memory_extraction"
    model_used: Mapped[str] = mapped_column(String(100), nullable=False)
    prompt_tokens: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    completion_tokens: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    estimated_cost_usd: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    used_fallback: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)


class BookingClick(UUIDPKMixin, TimestampMixin, Base):
    """One row per click-through to an external booking link on a trip item
    (Master Prompt §5 `booking_clicks`/`commissions`, Blueprint §57 "affiliate
    revenue"). Tour-Wayva has no booking/payment relationship with Amadeus —
    it cannot know a REAL commission was earned, only that a user clicked
    through with intent to book. `estimated_commission_usd` is therefore
    always an ESTIMATE from a configured rate table
    (ANALYTICS_COMMISSION_RATES), never a confirmed payout, and every read
    of it must say so (Blueprint §108: never pretend a booking/payment
    happened)."""

    __tablename__ = "booking_clicks"

    user_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True
    )
    trip_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True), ForeignKey("trips.id", ondelete="SET NULL"), nullable=True, index=True
    )
    trip_item_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True), ForeignKey("trip_items.id", ondelete="SET NULL"), nullable=True, index=True
    )
    item_type: Mapped[str] = mapped_column(String(20), nullable=False)  # hotel | flight | activity
    provider: Mapped[str] = mapped_column(String(50), nullable=False)   # e.g. "amadeus"
    estimated_value: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    estimated_value_currency: Mapped[str] = mapped_column(String(3), nullable=False, default="USD")
    estimated_commission_usd: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)


class AnalyticsEvent(UUIDPKMixin, TimestampMixin, Base):
    __tablename__ = "analytics_events"

    user_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True
    )
    event_type: Mapped[str] = mapped_column(String(100), nullable=False, index=True)
    properties: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)


class DailyMetric(UUIDPKMixin, TimestampMixin, Base):
    """One aggregated number per day per metric (Master Prompt §5 `daily_metrics`, §57).
    Written by the nightly aggregation job; idempotent — re-running a day overwrites it."""

    __tablename__ = "daily_metrics"
    __table_args__ = (UniqueConstraint("metric_date", "metric_key", name="uq_daily_metrics_date_key"),)

    metric_date: Mapped[date] = mapped_column(Date, nullable=False, index=True)
    metric_key: Mapped[str] = mapped_column(String(80), nullable=False)
    value: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)


class ProviderUsageRecord(UUIDPKMixin, TimestampMixin, Base):
    """One row per external provider CALL (Master Prompt §5 `provider_usage`, §78).
    Written by the resilience layer's observer (app/core/resilience.py `call_resilient`),
    so every provider that goes through it is covered automatically — no per-call-site
    instrumentation to forget. Distinct from `ai_usage_records` (token/cost detail for
    LLM calls specifically) and `api_usage` (OUR api, called by clients)."""

    __tablename__ = "provider_usage"

    provider: Mapped[str] = mapped_column(String(50), nullable=False, index=True)   # e.g. "amadeus_hotels", "weatherapi"
    success: Mapped[bool] = mapped_column(Boolean, nullable=False)
    duration_ms: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    circuit_state: Mapped[str] = mapped_column(String(20), nullable=False, default="closed")
    error_type: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)


class ApiUsageRecord(UUIDPKMixin, TimestampMixin, Base):
    """One row per INBOUND API request (Master Prompt §5 `api_usage`): who called what, how
    fast, and with what result. Written by middleware (app/main.py), sampled in production
    (API_USAGE_SAMPLE_RATE) since this is a high-volume table used for trend analysis, not
    per-request forensics (the existing security/audit logs cover that)."""

    __tablename__ = "api_usage"

    user_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True
    )
    method: Mapped[str] = mapped_column(String(10), nullable=False)
    path: Mapped[str] = mapped_column(String(255), nullable=False, index=True)   # route TEMPLATE, e.g. "/trips/{trip_id}"
    status_code: Mapped[int] = mapped_column(Integer, nullable=False)
    duration_ms: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
