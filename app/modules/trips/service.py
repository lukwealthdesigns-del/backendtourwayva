"""
TripService — trip lifecycle plus the "never trust the frontend"
ownership/membership checks every other trip-related module (Phase 3
itinerary, later Phase 5 collaboration) relies on.

On creation, a Trip is scaffolded with one TripDay per calendar day
in [start_date, end_date] (empty — no items yet). This is the
structured skeleton the future AI itinerary-generation pipeline
(Phase 4) will populate; today, items are added directly via the
Itinerary API (see app/modules/itinerary).
"""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from typing import Sequence

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.constants import TripMemberRole, TripStatus
from app.core.exceptions import ForbiddenError, NotFoundError
from app.db.models.trip import Trip, TripDay, TripMember
from app.modules.trips.schemas import TripCreateRequest
from app.repositories.trip_repository import TripRepository


class TripService:
    def __init__(self, db: AsyncSession):
        self.db = db
        self.repo = TripRepository(db)

    async def create_trip(self, *, owner_id: uuid.UUID, payload: TripCreateRequest) -> Trip:
        trip = Trip(
            owner_id=owner_id,
            title=payload.title,
            origin=payload.origin,
            destination=payload.destination,
            start_date=payload.start_date,
            end_date=payload.end_date,
            travelers=payload.travelers,
            budget_amount=payload.budget_amount,
            budget_currency=payload.budget_currency,
            status=TripStatus.DRAFT,
        )
        await self.repo.create_trip(trip)

        await self.repo.add_member(
            TripMember(
                trip_id=trip.id,
                user_id=owner_id,
                role=TripMemberRole.OWNER,
                joined_at=datetime.now(timezone.utc),
            )
        )

        # Scaffold one TripDay per day of the trip.
        num_days = (payload.end_date - payload.start_date).days + 1
        for day_number in range(1, num_days + 1):
            await self.repo.add_day(
                TripDay(
                    trip_id=trip.id,
                    day_number=day_number,
                    date=payload.start_date + timedelta(days=day_number - 1),
                )
            )

        await self.db.commit()
        return trip

    async def list_trips_for_user(self, user_id: uuid.UUID) -> Sequence[Trip]:
        return await self.repo.list_trips_for_user(user_id)

    async def list_trips_with_membership(self, user_id: uuid.UUID) -> list[tuple[Trip, TripMember]]:
        return await self.repo.list_trips_with_membership(user_id)

    async def set_archived(self, *, trip_id: uuid.UUID, user_id: uuid.UUID, archived: bool) -> tuple[Trip, TripMember]:
        """Archive/unarchive a trip FOR THIS MEMBER ONLY. Any member may do it (it is a personal
        filter, not a change to the trip), and repeating it is harmless."""
        trip = await self.get_trip_authorized(trip_id=trip_id, user_id=user_id)
        member = await self.repo.get_membership(trip_id, user_id)
        if member is None:  # pragma: no cover - get_trip_authorized just proved membership
            raise NotFoundError("Trip not found.")
        if archived and member.archived_at is None:
            member.archived_at = datetime.now(timezone.utc)
        elif not archived:
            member.archived_at = None
        await self.db.commit()
        return trip, member

    async def get_trip_authorized(
        self, *, trip_id: uuid.UUID, user_id: uuid.UUID, require_editor: bool = False
    ) -> Trip:
        """Central authorization checkpoint (Blueprint Principle 1:
        never trust the frontend). Every trip-scoped endpoint across
        trips/ and itinerary/ routes through this."""
        trip = await self.repo.get_trip(trip_id)
        if trip is None:
            raise NotFoundError("Trip not found.")

        membership = await self.repo.get_membership(trip_id, user_id)
        if membership is None:
            raise ForbiddenError("You do not have access to this trip.")

        if require_editor and membership.role not in (TripMemberRole.OWNER, TripMemberRole.EDITOR):
            raise ForbiddenError("You do not have permission to modify this trip.")

        return trip

    async def get_trip_day_authorized(
        self, *, day_id: uuid.UUID, user_id: uuid.UUID, require_editor: bool = False
    ) -> TripDay:
        day = await self.repo.get_day(day_id)
        if day is None:
            raise NotFoundError("Trip day not found.")
        await self.get_trip_authorized(trip_id=day.trip_id, user_id=user_id, require_editor=require_editor)
        return day

    # --- Member management (Master Blueprint §43) ---

    async def list_members(self, *, trip_id: uuid.UUID, user_id: uuid.UUID) -> Sequence[TripMember]:
        await self.get_trip_authorized(trip_id=trip_id, user_id=user_id)
        return await self.repo.list_members(trip_id)

    async def change_member_role(
        self, *, trip_id: uuid.UUID, target_user_id: uuid.UUID, new_role: TripMemberRole, actor_id: uuid.UUID
    ) -> TripMember:
        """Only the trip owner can change roles — editors managing
        other editors' access would be a privilege-escalation path,
        so this is deliberately narrower than the general
        `require_editor` gate used elsewhere."""
        await self._require_owner(trip_id=trip_id, actor_id=actor_id)

        member = await self.repo.get_membership(trip_id, target_user_id)
        if member is None:
            raise NotFoundError("This user is not a member of this trip.")

        if member.role == TripMemberRole.OWNER and new_role != TripMemberRole.OWNER:
            if await self.repo.count_owners(trip_id) <= 1:
                raise ForbiddenError("A trip must always have at least one owner.")

        member.role = new_role
        await self.repo.save_member(member)
        await self.db.commit()
        return member

    async def remove_member(self, *, trip_id: uuid.UUID, target_user_id: uuid.UUID, actor_id: uuid.UUID) -> None:
        await self._require_owner(trip_id=trip_id, actor_id=actor_id)

        member = await self.repo.get_membership(trip_id, target_user_id)
        if member is None:
            raise NotFoundError("This user is not a member of this trip.")

        if member.role == TripMemberRole.OWNER and await self.repo.count_owners(trip_id) <= 1:
            raise ForbiddenError("A trip must always have at least one owner — transfer ownership first.")

        await self.repo.remove_member(member)
        await self.db.commit()

    async def add_member_from_invitation(
        self, *, trip_id: uuid.UUID, user_id: uuid.UUID, role: TripMemberRole
    ) -> TripMember:
        """Used only by CollaborationService when an invitation is
        accepted — never exposed directly, since it bypasses the
        normal owner-only member-creation path on purpose (accepting
        your own invitation is the one legitimate way to self-add)."""
        existing = await self.repo.get_membership(trip_id, user_id)
        if existing is not None:
            raise ForbiddenError("You are already a member of this trip.")

        member = TripMember(trip_id=trip_id, user_id=user_id, role=role, joined_at=datetime.now(timezone.utc))
        await self.repo.add_member(member)
        await self.db.commit()
        return member

    async def _require_owner(self, *, trip_id: uuid.UUID, actor_id: uuid.UUID) -> None:
        membership = await self.repo.get_membership(trip_id, actor_id)
        if membership is None:
            raise NotFoundError("Trip not found.")
        if membership.role != TripMemberRole.OWNER:
            raise ForbiddenError("Only the trip owner can do this.")
