"""CommentService (Master Blueprint §43: Comments)."""
from __future__ import annotations

import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import ForbiddenError, NotFoundError
from app.db.models.collaboration import TripComment
from app.db.models.trip import TripMemberRole
from app.modules.trips.service import TripService
from app.repositories.collaboration_repository import CollaborationRepository


class CommentService:
    def __init__(self, db: AsyncSession):
        self.db = db
        self.repo = CollaborationRepository(db)
        self.trip_service = TripService(db)

    async def add_comment(self, *, trip_id: uuid.UUID, user_id: uuid.UUID, content: str) -> TripComment:
        # Any member (including viewers) may comment — commenting
        # isn't itinerary-mutating, so it doesn't need editor access.
        await self.trip_service.get_trip_authorized(trip_id=trip_id, user_id=user_id)

        comment = TripComment(trip_id=trip_id, user_id=user_id, content=content)
        await self.repo.create_comment(comment)
        await self.db.commit()
        return comment

    async def list_comments(self, *, trip_id: uuid.UUID, user_id: uuid.UUID):
        await self.trip_service.get_trip_authorized(trip_id=trip_id, user_id=user_id)
        return await self.repo.list_comments_for_trip(trip_id)

    async def delete_comment(self, *, comment_id: uuid.UUID, actor_id: uuid.UUID) -> None:
        comment = await self.repo.get_comment(comment_id)
        if comment is None:
            raise NotFoundError("Comment not found.")

        await self.trip_service.get_trip_authorized(trip_id=comment.trip_id, user_id=actor_id)
        actor_membership = await self.trip_service.repo.get_membership(comment.trip_id, actor_id)

        is_author = comment.user_id == actor_id
        is_owner = actor_membership is not None and actor_membership.role == TripMemberRole.OWNER
        if not (is_author or is_owner):
            raise ForbiddenError("You can only delete your own comments (or, as owner, anyone's).")

        await self.repo.delete_comment(comment)
        await self.db.commit()
