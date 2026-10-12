"""
Regenerate ONE day of an itinerary.

The person asks for a fresh plan for a single day (optionally with a note such as "more relaxed" or "add a museum"). This does
not invent a new pipeline: it asks the existing revision workflow (the same one the Companion uses) for a replacement of that
day, so the new day is held to exactly the same rules as any generated plan (verified places, no overlapping times, opening
hours, budget arithmetic). The result is only a PROPOSAL the person confirms or discards; nothing changes until they confirm.

  * Only the chosen day may change. If the model also touches another day the proposal is discarded and nothing is stored.
  * A guest's regenerations count against the guest generation allowance (they cost real money); accounts are rate limited.
"""
from __future__ import annotations

import uuid
from typing import Any, Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import GenerationQuotaError, NotFoundError, ValidationAppError
from app.db.models.conversation import Conversation
from app.db.models.user import User
from app.modules.planning.policy import GENERATION_EVENT, PlanningPolicyService
from app.modules.planning.day_scope import build_instruction
from app.modules.planning.revision_service import RevisionService
from app.modules.trips.service import TripService
from app.repositories.trip_repository import TripRepository

CHANGES_CONVERSATION_TITLE = "Itinerary changes"


class DayRegenerationService:
    def __init__(self, db: AsyncSession, *, llm: Any = None) -> None:
        self.db = db
        self.llm = llm
        self.repo = TripRepository(db)

    async def _changes_conversation(self, trip_id: uuid.UUID, user: User) -> Conversation:
        """The conversation proposals are attached to (one per person and trip, found again next time)."""
        found = (await self.db.execute(
            select(Conversation).where(
                Conversation.user_id == user.id, Conversation.trip_id == trip_id, Conversation.title == CHANGES_CONVERSATION_TITLE
            )
        )).scalars().first()
        if found is not None:
            return found
        conversation = Conversation(user_id=user.id, trip_id=trip_id, title=CHANGES_CONVERSATION_TITLE)
        self.db.add(conversation)
        await self.db.flush()
        return conversation

    async def _enforce_guest_allowance(self, user: User) -> None:
        if not user.is_guest:
            return
        policy = PlanningPolicyService(self.db)
        limits = await policy.limits_for(user.id)
        if limits.monthly_limit and await policy.generations_this_month(user.id) >= limits.monthly_limit:
            raise GenerationQuotaError(
                "You've used the free itinerary generations in this guest session. Create a free account to plan more.",
                {"tier": limits.tier, "limit": limits.monthly_limit},
            )

    async def propose(self, *, trip_id: uuid.UUID, day_id: uuid.UUID, user: User, note: Optional[str] = None) -> dict[str, Any]:
        trip = await TripService(self.db).get_trip_authorized(trip_id=trip_id, user_id=user.id, require_editor=True)
        day = await self.repo.get_day(day_id)
        if day is None or day.trip_id != trip.id:
            raise NotFoundError("Day not found.")
        if not list(await self.repo.list_items_for_day(day.id)):
            raise ValidationAppError("This day has no plan yet. Generate the itinerary first.")
        await self._enforce_guest_allowance(user)

        conversation = await self._changes_conversation(trip.id, user)
        await self.db.commit()   # keep the conversation even if the proposal below finds nothing to change
        result = await RevisionService(self.db, llm=self.llm).propose(
            conversation_id=conversation.id, trip_id=trip.id, user=user,
            instruction=build_instruction(day.day_number, day.date, note), only_day=day.day_number,
        )
        if result.get("changed") and user.is_guest:
            # A guest's regeneration is charged only when it produced a proposal.
            from app.modules.analytics.service import AnalyticsService

            await AnalyticsService(self.db).record_event(
                user_id=user.id, event_type=GENERATION_EVENT, properties={"trip_id": str(trip.id), "day": day.day_number, "regenerate_day": True},
            )
        return result
