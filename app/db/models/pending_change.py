"""
PendingItineraryChange (Master Blueprint §18, Principle 2: "AI
proposes, systems verify, users decide").

This is deliberately the ONLY way Companion can affect a trip. The AI
never calls ItineraryService.add_item/update_item/delete_item
directly — it can only create a pending, unapplied proposal here via
a `propose_*` tool (see app/modules/companion/tools.py). A human with
editor/owner access must explicitly confirm it — which runs it
through the exact same ItineraryService methods, and the exact same
ItineraryValidationService checks, as a manual edit made through the
UI — before anything actually changes.
"""
from __future__ import annotations

import uuid
from typing import Optional

from sqlalchemy import Enum, ForeignKey, String
from sqlalchemy.dialects.postgresql import JSON, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.constants import PendingChangeAction, PendingChangeStatus
from app.db.base import Base, TimestampMixin, UUIDPKMixin, enum_values


class PendingItineraryChange(UUIDPKMixin, TimestampMixin, Base):
    __tablename__ = "pending_itinerary_changes"

    conversation_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("conversations.id", ondelete="CASCADE"), nullable=False, index=True
    )
    trip_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("trips.id", ondelete="CASCADE"), nullable=False, index=True
    )
    action: Mapped[PendingChangeAction] = mapped_column(
        Enum(PendingChangeAction, name="pending_change_action_enum", values_callable=enum_values), nullable=False
    )
    day_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True), ForeignKey("trip_days.id", ondelete="CASCADE"), nullable=True
    )
    item_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True), ForeignKey("trip_items.id", ondelete="CASCADE"), nullable=True
    )
    payload: Mapped[dict] = mapped_column(JSON, nullable=False)
    summary: Mapped[str] = mapped_column(String(500), nullable=False)
    status: Mapped[PendingChangeStatus] = mapped_column(
        Enum(PendingChangeStatus, name="pending_change_status_enum", values_callable=enum_values),
        default=PendingChangeStatus.PENDING,
        nullable=False,
    )
    proposed_by: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=False)
    decided_by: Mapped[Optional[uuid.UUID]] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=True)
