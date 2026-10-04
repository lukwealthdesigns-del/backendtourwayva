"""
VoteService (Master Blueprint §43: "Voting where appropriate").

Voting is advisory only — see PendingChangeVote's docstring. Any trip
member may cast one vote per PendingItineraryChange (upsertable — a
second vote from the same user replaces their first, it doesn't add
a second ballot). Casting/viewing votes requires trip membership;
actually confirming or rejecting the change still requires
editor/owner access, exactly as Phase 4 built it.
"""
from __future__ import annotations

import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import NotFoundError
from app.modules.collaboration.schemas import VoteTallyResponse
from app.modules.companion.change_service import PendingChangeService
from app.modules.trips.service import TripService
from app.repositories.collaboration_repository import CollaborationRepository


class VoteService:
    def __init__(self, db: AsyncSession):
        self.db = db
        self.repo = CollaborationRepository(db)
        self.trip_service = TripService(db)
        self.change_service = PendingChangeService(db)

    async def cast_vote(self, *, change_id: uuid.UUID, user_id: uuid.UUID, is_upvote: bool) -> VoteTallyResponse:
        change = await self.change_service.get_change(change_id)
        # Any member may vote — voting doesn't mutate the trip, so no
        # editor requirement here (confirm/reject still requires it).
        await self.trip_service.get_trip_authorized(trip_id=change.trip_id, user_id=user_id)

        existing = await self.repo.get_vote(change_id, user_id)
        if existing is not None:
            existing.is_upvote = is_upvote
            await self.repo.save_vote(existing)
        else:
            from app.db.models.collaboration import PendingChangeVote

            await self.repo.create_vote(PendingChangeVote(change_id=change_id, user_id=user_id, is_upvote=is_upvote))

        await self.db.commit()
        return await self.get_tally(change_id=change_id, user_id=user_id)

    async def get_tally(self, *, change_id: uuid.UUID, user_id: uuid.UUID) -> VoteTallyResponse:
        votes = await self.repo.list_votes(change_id)
        upvotes = sum(1 for v in votes if v.is_upvote)
        downvotes = sum(1 for v in votes if not v.is_upvote)
        your_vote = next((v.is_upvote for v in votes if v.user_id == user_id), None)
        return VoteTallyResponse(change_id=change_id, upvotes=upvotes, downvotes=downvotes, your_vote=your_vote)
