"""
UserPreferences (Master Prompt §5 `user_preferences`, §7).

Collected during the OPTIONAL, skippable onboarding or edited later in
the profile. Every field is optional and only ever set from what the
user explicitly provides — sensitive preferences (dietary, accessibility)
are voluntary and are never inferred or assumed.
"""
from __future__ import annotations

import uuid
from typing import Optional

from sqlalchemy import ForeignKey, String
from sqlalchemy.dialects.postgresql import JSON, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin, UUIDPKMixin


class UserPreferences(UUIDPKMixin, TimestampMixin, Base):
    __tablename__ = "user_preferences"

    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), unique=True, nullable=False, index=True
    )
    travel_styles: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)
    interests: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)
    budget_preference: Mapped[Optional[str]] = mapped_column(String(30), nullable=True)
    accommodation_preference: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)
    transportation_preference: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)
    walking_preference: Mapped[Optional[str]] = mapped_column(String(20), nullable=True)
    dietary_preferences: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)
    accessibility_preferences: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)

    def __repr__(self) -> str:  # pragma: no cover
        return f"<UserPreferences user_id={self.user_id}>"
