"""
Security models (Master Blueprint §62-64).

These are the persisted, audit-able counterparts to the fast-path
Redis lockout in app/core/rate_limit.py: Redis enforces the lockout
in real time (and fails open if unreachable); these tables keep a
durable record that survives a Redis restart/flush and that an admin
can actually review (§89: "Security events" on the admin dashboard).
"""
from __future__ import annotations

import uuid
from datetime import datetime
from typing import Optional

from sqlalchemy import Boolean, DateTime, Enum, ForeignKey, String
from sqlalchemy.dialects.postgresql import JSON, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.constants import SecurityEventSeverity
from app.db.base import Base, TimestampMixin, UUIDPKMixin, enum_values


class LoginAttempt(UUIDPKMixin, TimestampMixin, Base):
    __tablename__ = "login_attempts"

    email: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    ip_address: Mapped[Optional[str]] = mapped_column(String(64), nullable=True, index=True)
    success: Mapped[bool] = mapped_column(Boolean, nullable=False)
    user_agent: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)


class BlockedIP(UUIDPKMixin, TimestampMixin, Base):
    __tablename__ = "blocked_ips"

    ip_address: Mapped[str] = mapped_column(String(64), unique=True, index=True, nullable=False)
    reason: Mapped[str] = mapped_column(String(500), nullable=False)
    blocked_by: Mapped[Optional[uuid.UUID]] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=True)
    expires_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)  # None = permanent


class SecurityEvent(UUIDPKMixin, TimestampMixin, Base):
    __tablename__ = "security_events"

    user_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True
    )
    event_type: Mapped[str] = mapped_column(String(100), nullable=False, index=True)
    severity: Mapped[SecurityEventSeverity] = mapped_column(
        Enum(SecurityEventSeverity, name="security_event_severity_enum", values_callable=enum_values), nullable=False
    )
    ip_address: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    extra_metadata: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)
