"""
Payments (Paystack) — Master Prompt §5 `subscription_events` plus the
records needed to collect money safely.

  Payment               one row per charge attempt/renewal; `reference` is
                        unique and is what the provider echoes back.
  SubscriptionEvent     append-only history of everything that happened to a
                        subscription (payments, renewals, provider events).
  PaymentWebhookEvent   one row per webhook delivery, keyed by the SHA-256 of
                        the raw body, so a redelivered event is a no-op.

Amounts are stored as integers in the currency's MINOR unit (kobo/cents),
never as floats.
"""
from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any, Optional

from sqlalchemy import BigInteger, Boolean, DateTime, Enum, ForeignKey, String
from sqlalchemy.dialects.postgresql import JSON, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.constants import PaymentStatus
from app.db.base import Base, TimestampMixin, UUIDPKMixin, enum_values


class Payment(UUIDPKMixin, TimestampMixin, Base):
    __tablename__ = "payments"

    # No ON DELETE rule on purpose: financial records outlive account
    # deletion (the users row is anonymized, never removed).
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id"), nullable=False, index=True
    )
    plan_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("plans.id"), nullable=False)
    subscription_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True), ForeignKey("subscriptions.id", ondelete="SET NULL"), nullable=True, index=True
    )
    provider: Mapped[str] = mapped_column(String(20), nullable=False, default="paystack")
    reference: Mapped[str] = mapped_column(String(100), unique=True, index=True, nullable=False)
    amount_minor: Mapped[int] = mapped_column(BigInteger, nullable=False)
    currency: Mapped[str] = mapped_column(String(3), nullable=False)
    status: Mapped[PaymentStatus] = mapped_column(
        Enum(PaymentStatus, name="payment_status_enum", values_callable=enum_values),
        nullable=False,
        default=PaymentStatus.PENDING,
        index=True,
    )
    is_renewal: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    paid_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    gateway_response: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    provider_customer_code: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    # Cumulative amount refunded so far, in MINOR units (never more than
    # amount_minor). A partial refund leaves `status` at SUCCESS with this
    # > 0; a full refund flips status to REFUNDED.
    refunded_amount_minor: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0, server_default="0")
    refunded_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)


class SubscriptionEvent(UUIDPKMixin, TimestampMixin, Base):
    __tablename__ = "subscription_events"

    user_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True
    )
    subscription_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True), ForeignKey("subscriptions.id", ondelete="SET NULL"), nullable=True, index=True
    )
    event_type: Mapped[str] = mapped_column(String(80), nullable=False, index=True)
    provider_reference: Mapped[Optional[str]] = mapped_column(String(100), nullable=True, index=True)
    details: Mapped[Optional[dict[str, Any]]] = mapped_column(JSON, nullable=True)


class PaymentWebhookEvent(UUIDPKMixin, TimestampMixin, Base):
    __tablename__ = "payment_webhook_events"

    event_key: Mapped[str] = mapped_column(String(64), unique=True, index=True, nullable=False)
    event_type: Mapped[str] = mapped_column(String(80), nullable=False)
