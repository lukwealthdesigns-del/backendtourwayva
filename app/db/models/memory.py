"""
UserMemory model (Master Blueprint §37).

Scoped for this delivery: memories are created directly via the API
(by the user, or by anything calling the endpoint as "system") rather
than automatically extracted from conversations — automatic
extraction needs the Companion to analyze message content with the
LLM and decide what's worth remembering, which is a Phase 4 hardening
step once Companion itself is further along (see
app/modules/companion). The storage model, ownership, and the
required user controls (view/edit/delete/disable) are fully
implemented now — extraction just isn't automatic yet.
"""
from __future__ import annotations

import uuid
from datetime import datetime
from typing import Optional

from sqlalchemy import Boolean, DateTime, Enum, Float, ForeignKey, String
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.constants import MemorySource
from app.db.base import Base, TimestampMixin, UUIDPKMixin, enum_values


class UserMemory(UUIDPKMixin, TimestampMixin, Base):
    __tablename__ = "user_memories"

    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    content: Mapped[str] = mapped_column(String(1000), nullable=False)
    source: Mapped[MemorySource] = mapped_column(Enum(MemorySource, name="memory_source_enum", values_callable=enum_values), nullable=False)
    confidence: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    is_enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    expires_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)

    def __repr__(self) -> str:  # pragma: no cover
        return f"<UserMemory id={self.id} user_id={self.user_id}>"
