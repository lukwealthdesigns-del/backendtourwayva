"""
TripInvitation model (Master Blueprint §43-44).

Invitations target an email address (the recipient may not have a
Tour-Wayva account yet) or, optionally, an existing user's @username
(recorded in `recipient_user_id`). Acceptance requires the authenticated user's
own email to match `recipient_email` — you cannot accept an
invitation addressed to someone else just because you know the token.

TripComment and PendingChangeVote (also §43: "Comments... Voting
where appropriate") live in this module too, since all three are
small collaboration primitives layered on top of the Phase 3
TripMember/role model and the Phase 4 PendingItineraryChange model.
"""
from __future__ import annotations

import uuid
from datetime import datetime
from typing import Optional

from sqlalchemy import Boolean, DateTime, Enum, ForeignKey, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.constants import InvitationStatus, TripMemberRole
from app.db.base import Base, TimestampMixin, UUIDPKMixin, enum_values


class TripInvitation(UUIDPKMixin, TimestampMixin, Base):
    __tablename__ = "trip_invitations"

    trip_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("trips.id", ondelete="CASCADE"), nullable=False, index=True
    )
    inviter_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=False)
    recipient_email: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    # Set only when the invitation was addressed by @username. `recipient_email` is still filled
    # (with that user's own email, so acceptance's email-match gate is unchanged), but the API
    # never shows it to the inviter — the whole point of a username invite is not exposing it.
    recipient_user_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True
    )
    role: Mapped[TripMemberRole] = mapped_column(
        Enum(TripMemberRole, name="trip_invitation_role_enum", values_callable=enum_values), nullable=False
    )
    token: Mapped[str] = mapped_column(String(64), unique=True, index=True, nullable=False)
    status: Mapped[InvitationStatus] = mapped_column(
        Enum(InvitationStatus, name="invitation_status_enum", values_callable=enum_values), default=InvitationStatus.PENDING, nullable=False
    )
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class TripComment(UUIDPKMixin, TimestampMixin, Base):
    __tablename__ = "trip_comments"

    trip_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("trips.id", ondelete="CASCADE"), nullable=False, index=True
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    content: Mapped[str] = mapped_column(Text, nullable=False)


class PendingChangeVote(UUIDPKMixin, TimestampMixin, Base):
    """Advisory only — voting does not auto-apply or auto-reject a
    PendingItineraryChange. Confirmation/rejection authority stays
    exactly where Phase 4 put it (editor/owner via
    PendingChangeService.confirm/reject); votes are visible context
    for that decision, not a substitute for it."""

    __tablename__ = "pending_change_votes"
    __table_args__ = (UniqueConstraint("change_id", "user_id", name="uq_pending_change_votes_change_user"),)

    change_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("pending_itinerary_changes.id", ondelete="CASCADE"), nullable=False, index=True
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    is_upvote: Mapped[bool] = mapped_column(Boolean, nullable=False)

