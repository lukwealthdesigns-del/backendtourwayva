"""
Monetization models (Master Blueprint §48-50).

IMPORTANT SCOPE NOTE: no payment processor (Stripe, Paystack, etc.)
was named anywhere in the Master Build Prompt — unlike Amadeus,
Brevo, Cloudinary, etc., which were explicit. Building a fake
integration with an unspecified provider would be exactly the kind
of fabrication Blueprint §108 forbids ("Do NOT pretend a payment
succeeded... Do NOT pretend a provider returned data"). So
Subscription here is a real, working STATE MACHINE — plans, active
subscriptions, cancellation — with no payment collection behind it.
`POST /subscriptions/subscribe` activates a subscription directly.
Wiring in a real payment provider means adding a `payments` provider
(the module is already scaffolded) that calls this exact same
Subscription state transition on a successful webhook, instead of
activating immediately on request.

Entitlement resolution itself is NOT a table — see
app/modules/entitlements/service.py's EntitlementService, which is
the "EntitlementService, not hardcoded `if premium`" the blueprint
asks for (§48).
"""
from __future__ import annotations

import uuid
from datetime import datetime
from typing import Optional

from sqlalchemy import Boolean, DateTime, Enum, Float, ForeignKey, Integer, String, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSON, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.constants import BillingInterval, FeatureFlag, SubscriptionStatus
from app.db.base import Base, TimestampMixin, UUIDPKMixin, enum_values


class Plan(UUIDPKMixin, TimestampMixin, Base):
    __tablename__ = "plans"

    name: Mapped[str] = mapped_column(String(100), nullable=False)
    slug: Mapped[str] = mapped_column(String(100), unique=True, index=True, nullable=False)
    price_amount: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    price_currency: Mapped[str] = mapped_column(String(3), nullable=False, default="USD")
    billing_interval: Mapped[BillingInterval] = mapped_column(
        Enum(BillingInterval, name="billing_interval_enum", values_callable=enum_values), nullable=False, default=BillingInterval.FREE
    )
    included_feature_flags: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    # Paystack plan code (PLN_...) for provider-managed recurring billing. Leave
    # empty for one-off purchases (the subscription simply ends at the period end).
    # The Paystack plan's amount/currency/interval MUST match this plan; charges
    # whose amount differs are rejected, never applied.
    paystack_plan_code: Mapped[Optional[str]] = mapped_column(String(64), nullable=True, unique=True)


class Subscription(UUIDPKMixin, TimestampMixin, Base):
    __tablename__ = "subscriptions"

    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    plan_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("plans.id"), nullable=False)
    status: Mapped[SubscriptionStatus] = mapped_column(
        Enum(SubscriptionStatus, name="subscription_status_enum", values_callable=enum_values), nullable=False, default=SubscriptionStatus.ACTIVE
    )
    current_period_start: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    current_period_end: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    cancelled_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    # False once the user cancelled (or the provider stopped renewing): the
    # subscription stays usable until current_period_end, then lapses.
    auto_renew: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    paystack_customer_code: Mapped[Optional[str]] = mapped_column(String(64), nullable=True, index=True)
    paystack_subscription_code: Mapped[Optional[str]] = mapped_column(String(64), nullable=True, index=True)
    paystack_email_token: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    # Lifecycle notifications, each sent at most once (see app/modules/lifecycle).
    reminder_sent_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    expired_notified_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)


class TrialConfig(UUIDPKMixin, TimestampMixin, Base):
    """Effectively a singleton — TrialConfigRepository always reads
    the most recently updated row, seeding a default on first read if
    none exists (Master Blueprint §49: admin-configurable trial
    enabled/disabled, duration, included features)."""

    __tablename__ = "trial_config"

    is_enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    duration_days: Mapped[int] = mapped_column(Integer, default=30, nullable=False)
    included_feature_flags: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)


class UserTrial(UUIDPKMixin, TimestampMixin, Base):
    __tablename__ = "user_trials"

    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), unique=True, nullable=False, index=True
    )
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    included_feature_flags: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)
    # Lifecycle notifications, each sent at most once (see app/modules/lifecycle).
    reminder_sent_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    expired_notified_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)


class FeatureFlagOverride(UUIDPKMixin, TimestampMixin, Base):
    """Per-user manual override — takes precedence over plan/trial
    resolution in EntitlementService (Master Blueprint §50)."""

    __tablename__ = "feature_flag_overrides"
    __table_args__ = (UniqueConstraint("user_id", "flag", name="uq_feature_flag_overrides_user_flag"),)

    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    flag: Mapped[FeatureFlag] = mapped_column(Enum(FeatureFlag, name="feature_flag_enum", values_callable=enum_values), nullable=False)
    is_enabled: Mapped[bool] = mapped_column(Boolean, nullable=False)


class FeatureFlagSetting(UUIDPKMixin, TimestampMixin, Base):
    """GLOBAL admin toggle for a feature flag (Master Prompt §5 `feature_flags`,
    Blueprint §50). No row = normal plan-based resolution.

      is_killed        off for EVERYONE (kill switch; beats every grant)
      is_open_to_all   on for EVERYONE regardless of plan (a per-user revocation still wins)
    """

    __tablename__ = "feature_flags"

    flag: Mapped[FeatureFlag] = mapped_column(
        Enum(FeatureFlag, name="feature_flag_enum", values_callable=enum_values), unique=True, nullable=False
    )
    is_killed: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    is_open_to_all: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    note: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    updated_by: Mapped[Optional[uuid.UUID]] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=True)
