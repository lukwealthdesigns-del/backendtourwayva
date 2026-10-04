"""
Trip + structured itinerary models (Master Blueprint §15, §19,
§43-44 subset implemented in Phase 3).

Deliberately scoped for this delivery:
  - Trip, TripMember, TripDay, TripItem, TripVersion are implemented.
  - trip_invitations, trip_routes, trip_notes, trip_costs, and
    trip_preferences (separate tables in the full blueprint) are
    NOT yet implemented — trip-level budget/currency live directly
    on Trip for now, and collaboration invitations are deferred to
    Phase 5. See app/modules/collaboration (scaffolded).

Itinerary generation is AI/LangGraph-driven per the blueprint
(Phase 4, not yet built) — so today, TripItems are created directly
via the API (by a user, or later by the AI once Phase 4 lands) rather
than auto-generated. The structured data model below is exactly what
that future AI pipeline will write into.
"""
from __future__ import annotations

import uuid
from datetime import date, datetime, time
from typing import Optional

from sqlalchemy import (
    Date,
    DateTime,
    Enum,
    Float,
    ForeignKey,
    Integer,
    JSON,
    String,
    Text,
    Time,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.constants import TripItemType, TripMemberRole, TripStatus
from app.db.base import Base, TimestampMixin, UUIDPKMixin, enum_values


class Trip(UUIDPKMixin, TimestampMixin, Base):
    __tablename__ = "trips"

    owner_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    origin: Mapped[Optional[str]] = mapped_column(String(200), nullable=True)
    destination: Mapped[str] = mapped_column(String(200), nullable=False)
    start_date: Mapped[date] = mapped_column(Date, nullable=False)
    end_date: Mapped[date] = mapped_column(Date, nullable=False)
    travelers: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    budget_amount: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    budget_currency: Mapped[Optional[str]] = mapped_column(String(3), nullable=True)
    status: Mapped[TripStatus] = mapped_column(
        Enum(TripStatus, name="trip_status_enum", values_callable=enum_values), default=TripStatus.DRAFT, nullable=False
    )
    # AI-written summary of the trip (set by itinerary generation; user-editable later).
    overview: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    current_version_number: Mapped[int] = mapped_column(Integer, default=1, nullable=False)

    def __repr__(self) -> str:  # pragma: no cover
        return f"<Trip id={self.id} destination={self.destination}>"


class TripMember(UUIDPKMixin, TimestampMixin, Base):
    __tablename__ = "trip_members"
    __table_args__ = (UniqueConstraint("trip_id", "user_id", name="uq_trip_members_trip_user"),)

    trip_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("trips.id", ondelete="CASCADE"), nullable=False, index=True
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    role: Mapped[TripMemberRole] = mapped_column(
        Enum(TripMemberRole, name="trip_member_role_enum", values_callable=enum_values), nullable=False
    )
    joined_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    # Archiving is PER MEMBER: it only hides the trip from that member's own lists (a viewer
    # archiving a shared trip must not hide it from the owner). NULL = not archived.
    archived_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)


class TripDay(UUIDPKMixin, TimestampMixin, Base):
    __tablename__ = "trip_days"
    __table_args__ = (UniqueConstraint("trip_id", "day_number", name="uq_trip_days_trip_day_number"),)

    trip_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("trips.id", ondelete="CASCADE"), nullable=False, index=True
    )
    day_number: Mapped[int] = mapped_column(Integer, nullable=False)
    date: Mapped[date] = mapped_column(Date, nullable=False)
    weather_summary: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)


class TripItem(UUIDPKMixin, TimestampMixin, Base):
    __tablename__ = "trip_items"

    trip_day_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("trip_days.id", ondelete="CASCADE"), nullable=False, index=True
    )
    item_type: Mapped[TripItemType] = mapped_column(
        Enum(TripItemType, name="trip_item_type_enum", values_callable=enum_values), nullable=False
    )
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    location_name: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    latitude: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    longitude: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    start_time: Mapped[Optional[time]] = mapped_column(Time, nullable=True)
    end_time: Mapped[Optional[time]] = mapped_column(Time, nullable=True)
    estimated_cost: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    currency: Mapped[Optional[str]] = mapped_column(String(3), nullable=True)
    provider: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)  # e.g. "amadeus", "manual"
    sort_order: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    notes: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    # Provenance (Master Prompt §15): "provider" = came from a live provider result,
    # "estimated" = an AI/user estimate, "manual" = typed in by a user.
    source: Mapped[Optional[str]] = mapped_column(String(20), nullable=True)
    external_id: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    booking_link: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)
    image_url: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)


class TripVersion(UUIDPKMixin, TimestampMixin, Base):
    """Full snapshot of a trip's days+items at a point in time
    (Master Blueprint §19: never destructively overwrite). Restoring
    a version replaces current TripDay/TripItem rows with the
    snapshot's contents and creates a NEW version recording that
    restoration — history is append-only, never edited in place."""

    __tablename__ = "trip_versions"
    __table_args__ = (UniqueConstraint("trip_id", "version_number", name="uq_trip_versions_trip_version"),)

    trip_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("trips.id", ondelete="CASCADE"), nullable=False, index=True
    )
    version_number: Mapped[int] = mapped_column(Integer, nullable=False)
    created_by: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=False)
    change_summary: Mapped[str] = mapped_column(String(500), nullable=False)
    snapshot: Mapped[dict] = mapped_column(JSON, nullable=False)
    parent_version_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True), ForeignKey("trip_versions.id"), nullable=True
    )


class TripNote(UUIDPKMixin, TimestampMixin, Base):
    """Free-form notes on a trip (Master Blueprint's trip_notes table)
    — distinct from TripComment (app/db/models/collaboration.py):
    notes are for things like "remember the passport expires in
    March" rather than a threaded discussion. Visible to all trip
    members, same as comments; not per-user-private."""

    __tablename__ = "trip_notes"

    trip_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("trips.id", ondelete="CASCADE"), nullable=False, index=True
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    content: Mapped[str] = mapped_column(Text, nullable=False)


class TripCost(UUIDPKMixin, TimestampMixin, Base):
    """Itemized cost entries (Master Blueprint's trip_costs table) —
    distinct from the single Trip.budget_amount planning figure:
    these are actual/tracked costs a member logs against the trip
    (e.g. "Flights - $450", "Hotel deposit - $120"), which
    TripService sums for a real budget-vs-actual comparison."""

    __tablename__ = "trip_costs"

    trip_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("trips.id", ondelete="CASCADE"), nullable=False, index=True
    )
    added_by: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=False)
    category: Mapped[str] = mapped_column(String(50), nullable=False)  # free-form: "flights", "hotels", "food", ...
    description: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    amount: Mapped[float] = mapped_column(Float, nullable=False)
    currency: Mapped[str] = mapped_column(String(3), nullable=False)


class TripRoute(UUIDPKMixin, TimestampMixin, Base):
    """Cached route calculation between two trip items (Master
    Blueprint's trip_routes table) — populated on demand by calling
    MapsService (app/modules/maps) between an origin and destination
    TripItem, then cached here so re-viewing the same day's itinerary
    doesn't re-hit the routing provider every time."""

    __tablename__ = "trip_routes"

    trip_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("trips.id", ondelete="CASCADE"), nullable=False, index=True
    )
    origin_item_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("trip_items.id", ondelete="CASCADE"), nullable=False)
    destination_item_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("trip_items.id", ondelete="CASCADE"), nullable=False)
    mode: Mapped[str] = mapped_column(String(20), nullable=False)  # TravelMode value, e.g. "walking"
    distance_meters: Mapped[float] = mapped_column(Float, nullable=False)
    duration_seconds: Mapped[float] = mapped_column(Float, nullable=False)


class TripPreferences(UUIDPKMixin, TimestampMixin, Base):
    """Planner inputs that belong to a trip (Master Prompt §5 `trip_preferences`,
    §13): reused when the itinerary is regenerated."""

    __tablename__ = "trip_preferences"

    trip_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("trips.id", ondelete="CASCADE"), unique=True, nullable=False, index=True
    )
    travel_style: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)
    interests: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)
    pace: Mapped[Optional[str]] = mapped_column(String(20), nullable=True)               # relaxed | balanced | packed
    walking_preference: Mapped[Optional[str]] = mapped_column(String(20), nullable=True)  # low | moderate | high
    hotel_preference: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)
    transport_preference: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)
    food_preferences: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)
    must_see: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)
    avoid: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)
