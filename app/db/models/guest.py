"""
GuestSession: one row per guest ("Explore first. Commit later.").

The browser holds an opaque, unguessable token; only its SHA-256 hash is stored here. The session points at a guest USER
row (users.is_guest) that owns the guest's trips and conversations, so every existing ownership check and row-level
security policy applies to guests exactly as it does to anyone else. Claiming moves that data to a real account.
"""
from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any, Optional

from sqlalchemy import DateTime, ForeignKey, Integer, String
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin, UUIDPKMixin


class GuestSession(UUIDPKMixin, TimestampMixin, Base):
    __tablename__ = "guest_sessions"

    token_hash: Mapped[str] = mapped_column(String(64), unique=True, index=True, nullable=False)
    guest_user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), unique=True, nullable=False
    )
    status: Mapped[str] = mapped_column(String(16), default="active", nullable=False, index=True)  # active | claimed
    last_activity_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, index=True)
    claimed_by_user_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    claimed_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    companion_messages_used: Mapped[int] = mapped_column(Integer, default=0, server_default="0", nullable=False)
    ip_hash: Mapped[Optional[str]] = mapped_column(String(64), nullable=True, index=True)  # abuse signal only, never identity
    claim_summary: Mapped[Optional[dict[str, Any]]] = mapped_column(JSONB, nullable=True)
