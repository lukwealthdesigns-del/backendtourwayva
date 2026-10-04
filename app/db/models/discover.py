"""
Discover models (Master Blueprint §10-12).

DiscoverySearch records the request (for history/reuse); each
DiscoveryResult is one recommended destination from that search,
snapshotted with the cost estimate and score it had at generation
time — re-running the same search later can produce different
numbers (currency rates move, the AI's candidate set can vary), and
these rows are a durable record of what a person actually saw,
not a live view that would silently change underneath them.
"""
from __future__ import annotations

import uuid
from typing import Optional

from sqlalchemy import Float, ForeignKey, String
from sqlalchemy.dialects.postgresql import JSON, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin, UUIDPKMixin


class DiscoverySearch(UUIDPKMixin, TimestampMixin, Base):
    __tablename__ = "discovery_searches"

    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    request_params: Mapped[dict] = mapped_column(JSON, nullable=False)


class DiscoveryResult(UUIDPKMixin, TimestampMixin, Base):
    __tablename__ = "discovery_results"

    search_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("discovery_searches.id", ondelete="CASCADE"), nullable=False, index=True
    )
    destination: Mapped[str] = mapped_column(String(200), nullable=False)
    country: Mapped[Optional[str]] = mapped_column(String(2), nullable=True)
    latitude: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    longitude: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    estimated_total_cost: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    estimated_cost_currency: Mapped[Optional[str]] = mapped_column(String(3), nullable=True)
    score: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    reasons: Mapped[Optional[str]] = mapped_column(String(1000), nullable=True)
    extra_data: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)
