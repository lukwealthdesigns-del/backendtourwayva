"""
Saved places (Master Blueprint §75, and the Companion's `get_saved_places` /
`save_place` tools referenced in the build prompt) — a user's personal bookmark
list of destinations, hotels and activities they want to remember, independent
of any trip.

Denormalized (name/category/city/country/coordinates/source snapshotted onto
the row) rather than a foreign key to `places`, because most of what a user
saves comes from a live provider result (a Discover destination, an Amadeus
hotel, an Amadeus/OpenTripMap activity) that was never persisted as a `Place`
row — saving must not require ingesting into the shared reference table first.
`external_ref` + `source` let a later lookup re-fetch live details when useful
(e.g. re-searching that hotel for fresh availability).
"""
from __future__ import annotations

import uuid
from typing import Optional

from sqlalchemy import Enum, Float, ForeignKey, String, Text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.constants import PlaceCategory
from app.db.base import Base, TimestampMixin, UUIDPKMixin, enum_values


class SavedPlace(UUIDPKMixin, TimestampMixin, Base):
    __tablename__ = "saved_places"

    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    category: Mapped[PlaceCategory] = mapped_column(
        Enum(PlaceCategory, name="place_category_enum", values_callable=enum_values), nullable=False
    )
    city: Mapped[Optional[str]] = mapped_column(String(200), nullable=True)
    country: Mapped[Optional[str]] = mapped_column(String(2), nullable=True)
    latitude: Mapped[float] = mapped_column(Float, nullable=False)
    longitude: Mapped[float] = mapped_column(Float, nullable=False)
    notes: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    source: Mapped[str] = mapped_column(String(50), default="manual", nullable=False)
    external_ref: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    # Optional: the trip the user was planning/viewing when they saved this (context only —
    # never required, and the place is NOT deleted if that trip is).
    trip_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True), ForeignKey("trips.id", ondelete="SET NULL"), nullable=True
    )

    def __repr__(self) -> str:  # pragma: no cover
        return f"<SavedPlace id={self.id} name={self.name}>"
