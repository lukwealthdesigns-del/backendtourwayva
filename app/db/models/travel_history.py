"""
Travel history models (Master Blueprint §45).

Populated when a trip is marked completed (see
app/modules/travel_history/service.py — `mark_trip_completed`
requires trip-owner access, flips Trip.status to COMPLETED, and
snapshots a VisitedDestination plus one VisitedPlace per itinerary
item). This is an explicit action, not automatic on the trip's end
date passing — a trip isn't "history" until someone says it happened.

§45's example queries ("What was my last trip to France?", "What
hotel did I use?") are answered by querying these structured tables
first (see TravelHistoryService.find_last_trip_to), per the
blueprint's own instruction: "The backend should query structured
travel-history records before asking the LLM to reason."
"""
from __future__ import annotations

import uuid
from datetime import date
from typing import Optional

from sqlalchemy import Date, Float, ForeignKey, String
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin, UUIDPKMixin


class VisitedDestination(UUIDPKMixin, TimestampMixin, Base):
    __tablename__ = "visited_destinations"

    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    trip_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True), ForeignKey("trips.id", ondelete="SET NULL"), nullable=True
    )
    destination: Mapped[str] = mapped_column(String(200), nullable=False, index=True)
    start_date: Mapped[date] = mapped_column(Date, nullable=False)
    end_date: Mapped[date] = mapped_column(Date, nullable=False)


class VisitedPlace(UUIDPKMixin, TimestampMixin, Base):
    __tablename__ = "visited_places"

    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    trip_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True), ForeignKey("trips.id", ondelete="SET NULL"), nullable=True
    )
    place_name: Mapped[str] = mapped_column(String(255), nullable=False)
    category: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)  # copied from TripItemType
    visited_date: Mapped[Optional[date]] = mapped_column(Date, nullable=True)
    estimated_cost: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    currency: Mapped[Optional[str]] = mapped_column(String(3), nullable=True)
