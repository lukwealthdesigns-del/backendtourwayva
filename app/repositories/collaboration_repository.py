"""Repository for invitations, comments, and pending-change votes."""
from __future__ import annotations

import uuid
from typing import Optional, Sequence

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.constants import InvitationStatus
from app.db.models.collaboration import PendingChangeVote, TripComment, TripInvitation


class CollaborationRepository:
    def __init__(self, db: AsyncSession):
        self.db = db

    # --- Invitations ---
    async def create_invitation(self, invitation: TripInvitation) -> TripInvitation:
        self.db.add(invitation)
        await self.db.flush()
        return invitation

    async def get_invitation_by_token(self, token: str) -> Optional[TripInvitation]:
        result = await self.db.execute(select(TripInvitation).where(TripInvitation.token == token))
        return result.scalar_one_or_none()

    async def get_invitation(self, invitation_id: uuid.UUID) -> Optional[TripInvitation]:
        result = await self.db.execute(select(TripInvitation).where(TripInvitation.id == invitation_id))
        return result.scalar_one_or_none()

    async def list_invitations_for_trip(self, trip_id: uuid.UUID) -> Sequence[TripInvitation]:
        result = await self.db.execute(
            select(TripInvitation).where(TripInvitation.trip_id == trip_id).order_by(TripInvitation.created_at.desc())
        )
        return result.scalars().all()

    async def save_invitation(self, invitation: TripInvitation) -> TripInvitation:
        await self.db.flush()
        return invitation

    # --- Comments ---
    async def create_comment(self, comment: TripComment) -> TripComment:
        self.db.add(comment)
        await self.db.flush()
        return comment

    async def get_comment(self, comment_id: uuid.UUID) -> Optional[TripComment]:
        result = await self.db.execute(select(TripComment).where(TripComment.id == comment_id))
        return result.scalar_one_or_none()

    async def list_comments_for_trip(self, trip_id: uuid.UUID) -> Sequence[TripComment]:
        result = await self.db.execute(
            select(TripComment).where(TripComment.trip_id == trip_id).order_by(TripComment.created_at)
        )
        return result.scalars().all()

    async def delete_comment(self, comment: TripComment) -> None:
        await self.db.delete(comment)
        await self.db.flush()

    # --- Votes ---
    async def get_vote(self, change_id: uuid.UUID, user_id: uuid.UUID) -> Optional[PendingChangeVote]:
        result = await self.db.execute(
            select(PendingChangeVote).where(
                PendingChangeVote.change_id == change_id, PendingChangeVote.user_id == user_id
            )
        )
        return result.scalar_one_or_none()

    async def list_votes(self, change_id: uuid.UUID) -> Sequence[PendingChangeVote]:
        result = await self.db.execute(select(PendingChangeVote).where(PendingChangeVote.change_id == change_id))
        return result.scalars().all()

    async def create_vote(self, vote: PendingChangeVote) -> PendingChangeVote:
        self.db.add(vote)
        await self.db.flush()
        return vote

    async def save_vote(self, vote: PendingChangeVote) -> PendingChangeVote:
        await self.db.flush()
        return vote

    # --- Invitee-side lookups (@username invites; "my invitations") ---
    async def list_pending_for_recipient(
        self, *, user_id: uuid.UUID, email: str, now
    ) -> Sequence[TripInvitation]:
        """Live (pending, unexpired) invitations addressed to this account — by @username link or
        by the account's email address."""
        from sqlalchemy import func, or_

        result = await self.db.execute(
            select(TripInvitation)
            .where(
                TripInvitation.status == InvitationStatus.PENDING,
                TripInvitation.expires_at > now,
                or_(
                    TripInvitation.recipient_user_id == user_id,
                    func.lower(TripInvitation.recipient_email) == email.lower(),
                ),
            )
            .order_by(TripInvitation.created_at.desc())
        )
        return result.scalars().all()

    async def get_live_pending_invitation(
        self, *, trip_id: uuid.UUID, email: str, now
    ) -> Optional[TripInvitation]:
        from sqlalchemy import func

        result = await self.db.execute(
            select(TripInvitation).where(
                TripInvitation.trip_id == trip_id,
                TripInvitation.status == InvitationStatus.PENDING,
                TripInvitation.expires_at > now,
                func.lower(TripInvitation.recipient_email) == email.lower(),
            )
        )
        return result.scalars().first()

    async def get_trips_by_ids(self, trip_ids) -> dict:
        from app.db.models.trip import Trip

        ids = list({t for t in trip_ids})
        if not ids:
            return {}
        result = await self.db.execute(select(Trip).where(Trip.id.in_(ids)))
        return {t.id: t for t in result.scalars().all()}
