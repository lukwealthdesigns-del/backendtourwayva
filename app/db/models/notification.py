"""
Notification models (Master Blueprint §46-47).

Distinct from AdminMessage (Phase 7, §52): AdminMessage is a direct
human-to-human message from staff; Notification is a system-generated
event record (a trip invitation arrived, an itinerary change was
proposed, a trial is expiring). Both can render in the same "inbox"
UI, but they're semantically different — one has a human sender, the
other doesn't.

EmailLog is the durable record behind every outbound email
(Blueprint §47's own model list) — see
app/providers/email/brevo_provider.py, which writes one row per send
attempt when given a db session.
"""
from __future__ import annotations

import uuid
from datetime import datetime
from typing import Optional

from sqlalchemy import Boolean, DateTime, Enum, ForeignKey, String, Text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.constants import NotificationChannel, NotificationType
from app.db.base import Base, TimestampMixin, UUIDPKMixin, enum_values


class Notification(UUIDPKMixin, TimestampMixin, Base):
    __tablename__ = "notifications"

    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    notification_type: Mapped[NotificationType] = mapped_column(
        Enum(NotificationType, name="notification_type_enum", values_callable=enum_values), nullable=False
    )
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    body: Mapped[str] = mapped_column(Text, nullable=False)
    link: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)  # e.g. a trip_id or change_id reference
    read_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)


class NotificationPreference(UUIDPKMixin, TimestampMixin, Base):
    """One row per user (created lazily on first write, default
    all-enabled) — not one row per notification type, to keep this
    simple; per-type granularity is a reasonable future increment if
    users actually ask for it."""

    __tablename__ = "notification_preferences"

    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), unique=True, nullable=False, index=True
    )
    email_enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    in_app_enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)


class EmailLog(UUIDPKMixin, TimestampMixin, Base):
    __tablename__ = "email_logs"

    to_email: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    subject: Mapped[str] = mapped_column(String(500), nullable=False)
    category: Mapped[str] = mapped_column(String(50), nullable=False)  # e.g. "otp", "invitation", "notification"
    success: Mapped[bool] = mapped_column(Boolean, nullable=False)
    error_message: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
