"""
TripShareLink: a public, read-only link to a trip (anyone holding the link can open it).
ContentReport: a reader's report about something public (today: a shared trip), worked through by moderators.

Only the SHA-256 of a link's token is stored. The owner id is kept on the link so a public read can run as the owner (row-level
security) without any signed-in caller.
"""
from __future__ import annotations

import uuid
from datetime import datetime
from typing import Optional

from sqlalchemy import DateTime, ForeignKey, Integer, String
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin, UUIDPKMixin


class TripShareLink(UUIDPKMixin, TimestampMixin, Base):
    __tablename__ = "trip_share_links"

    trip_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("trips.id", ondelete="CASCADE"), nullable=False, index=True)
    trip_owner_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    token_hash: Mapped[str] = mapped_column(String(64), unique=True, index=True, nullable=False)
    created_by: Mapped[Optional[uuid.UUID]] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    expires_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    revoked_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    revoked_reason: Mapped[Optional[str]] = mapped_column(String(20), nullable=True)   # "owner" | "replaced" | "moderation"
    view_count: Mapped[int] = mapped_column(Integer, default=0, server_default="0", nullable=False)
    last_viewed_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)


class ContentReport(UUIDPKMixin, TimestampMixin, Base):
    __tablename__ = "content_reports"

    link_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("trip_share_links.id", ondelete="CASCADE"), nullable=False, index=True)
    trip_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("trips.id", ondelete="CASCADE"), nullable=False, index=True)
    trip_title: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)         # what was reported, as it was then
    trip_destination: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    reason: Mapped[str] = mapped_column(String(20), nullable=False)
    details: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)
    reporter_ip_hash: Mapped[Optional[str]] = mapped_column(String(64), nullable=True, index=True)
    status: Mapped[str] = mapped_column(String(12), default="open", server_default="open", nullable=False, index=True)   # open | actioned | dismissed
    resolved_by: Mapped[Optional[uuid.UUID]] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    resolved_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    resolution_note: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)
