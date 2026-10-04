"""
Place reference data (Master Blueprint §75: 'places' as a searchable
reference table backing Discover/Planning). Scoped simply for this
delivery: a place is a named point of interest with a category,
location, and optional description, sourced either from another
provider's response (e.g. a geocoding or activity lookup worth
persisting for reuse) or curated directly.

NOT yet implemented: automatic population from Amadeus/OpenCage
results (would require hooking into those services' response
pipelines) or admin-gated curation (needs Phase 7 RBAC) — creation is
open to any authenticated user for now, clearly documented as a
temporary state in the README.
"""
from __future__ import annotations

from typing import Optional

from sqlalchemy import Enum, Float, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.core.constants import PlaceCategory
from app.db.base import Base, TimestampMixin, UUIDPKMixin, enum_values


class Place(UUIDPKMixin, TimestampMixin, Base):
    __tablename__ = "places"

    name: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    category: Mapped[PlaceCategory] = mapped_column(Enum(PlaceCategory, name="place_category_enum", values_callable=enum_values), nullable=False)
    city: Mapped[Optional[str]] = mapped_column(String(200), nullable=True, index=True)
    country: Mapped[Optional[str]] = mapped_column(String(2), nullable=True)  # ISO 3166-1 alpha-2
    latitude: Mapped[float] = mapped_column(Float, nullable=False)
    longitude: Mapped[float] = mapped_column(Float, nullable=False)
    description: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    source: Mapped[str] = mapped_column(String(50), default="manual", nullable=False)  # e.g. "manual", "amadeus"
    external_ref: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)

    def __repr__(self) -> str:  # pragma: no cover
        return f"<Place id={self.id} name={self.name}>"
