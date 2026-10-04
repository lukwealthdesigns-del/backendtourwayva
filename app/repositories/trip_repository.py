"""Trip + membership + day + item + version repository."""
from __future__ import annotations

import uuid
from typing import Optional, Sequence

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.constants import TripMemberRole
from app.db.models.trip import Trip, TripDay, TripItem, TripMember, TripVersion


class TripRepository:
    def __init__(self, db: AsyncSession):
        self.db = db

    # --- Trips ---
    async def create_trip(self, trip: Trip) -> Trip:
        self.db.add(trip)
        await self.db.flush()
        return trip

    async def get_trip(self, trip_id: uuid.UUID) -> Optional[Trip]:
        result = await self.db.execute(select(Trip).where(Trip.id == trip_id))
        return result.scalar_one_or_none()

    async def list_trips_for_user(self, user_id: uuid.UUID) -> Sequence[Trip]:
        result = await self.db.execute(
            select(Trip)
            .join(TripMember, TripMember.trip_id == Trip.id)
            .where(TripMember.user_id == user_id)
            .order_by(Trip.start_date.desc())
        )
        return result.scalars().all()

    async def list_trips_with_membership(self, user_id: uuid.UUID) -> list[tuple[Trip, TripMember]]:
        """Every trip the user belongs to, with THEIR membership row (role, archived_at)."""
        result = await self.db.execute(
            select(Trip, TripMember)
            .join(TripMember, TripMember.trip_id == Trip.id)
            .where(TripMember.user_id == user_id)
            .order_by(Trip.start_date.desc())
        )
        return [(trip, member) for trip, member in result.all()]

    async def save_trip(self, trip: Trip) -> Trip:
        await self.db.flush()
        return trip

    # --- Members ---
    async def add_member(self, member: TripMember) -> TripMember:
        self.db.add(member)
        await self.db.flush()
        return member

    async def get_membership(self, trip_id: uuid.UUID, user_id: uuid.UUID) -> Optional[TripMember]:
        result = await self.db.execute(
            select(TripMember).where(TripMember.trip_id == trip_id, TripMember.user_id == user_id)
        )
        return result.scalar_one_or_none()

    async def list_members(self, trip_id: uuid.UUID) -> Sequence[TripMember]:
        result = await self.db.execute(
            select(TripMember).where(TripMember.trip_id == trip_id).order_by(TripMember.joined_at)
        )
        return result.scalars().all()

    async def count_owners(self, trip_id: uuid.UUID) -> int:
        from app.core.constants import TripMemberRole

        result = await self.db.execute(
            select(TripMember).where(TripMember.trip_id == trip_id, TripMember.role == TripMemberRole.OWNER)
        )
        return len(result.scalars().all())

    async def remove_member(self, member: TripMember) -> None:
        await self.db.delete(member)
        await self.db.flush()

    async def save_member(self, member: TripMember) -> TripMember:
        await self.db.flush()
        return member

    # --- Days ---
    async def add_day(self, day: TripDay) -> TripDay:
        self.db.add(day)
        await self.db.flush()
        return day

    async def get_day(self, day_id: uuid.UUID) -> Optional[TripDay]:
        result = await self.db.execute(select(TripDay).where(TripDay.id == day_id))
        return result.scalar_one_or_none()

    async def list_days_for_trip(self, trip_id: uuid.UUID) -> Sequence[TripDay]:
        result = await self.db.execute(
            select(TripDay).where(TripDay.trip_id == trip_id).order_by(TripDay.day_number)
        )
        return result.scalars().all()

    async def get_day_by_number(self, trip_id: uuid.UUID, day_number: int) -> Optional[TripDay]:
        result = await self.db.execute(
            select(TripDay).where(TripDay.trip_id == trip_id, TripDay.day_number == day_number)
        )
        return result.scalar_one_or_none()

    # --- Items ---
    async def add_item(self, item: TripItem) -> TripItem:
        self.db.add(item)
        await self.db.flush()
        return item

    async def get_item(self, item_id: uuid.UUID) -> Optional[TripItem]:
        result = await self.db.execute(select(TripItem).where(TripItem.id == item_id))
        return result.scalar_one_or_none()

    async def list_items_for_day(self, trip_day_id: uuid.UUID) -> Sequence[TripItem]:
        result = await self.db.execute(
            select(TripItem).where(TripItem.trip_day_id == trip_day_id).order_by(TripItem.sort_order)
        )
        return result.scalars().all()

    async def list_items_for_trip(self, trip_id: uuid.UUID) -> Sequence[TripItem]:
        result = await self.db.execute(
            select(TripItem)
            .join(TripDay, TripDay.id == TripItem.trip_day_id)
            .where(TripDay.trip_id == trip_id)
            .order_by(TripDay.day_number, TripItem.sort_order)
        )
        return result.scalars().all()

    async def delete_item(self, item: TripItem) -> None:
        await self.db.delete(item)
        await self.db.flush()

    async def save_item(self, item: TripItem) -> TripItem:
        await self.db.flush()
        return item

    # --- Versions ---
    async def add_version(self, version: TripVersion) -> TripVersion:
        self.db.add(version)
        await self.db.flush()
        return version

    async def list_versions(self, trip_id: uuid.UUID) -> Sequence[TripVersion]:
        result = await self.db.execute(
            select(TripVersion).where(TripVersion.trip_id == trip_id).order_by(TripVersion.version_number.desc())
        )
        return result.scalars().all()

    async def get_version(self, version_id: uuid.UUID) -> Optional[TripVersion]:
        result = await self.db.execute(select(TripVersion).where(TripVersion.id == version_id))
        return result.scalar_one_or_none()


def is_editor_or_above(role: TripMemberRole) -> bool:
    return role in (TripMemberRole.OWNER, TripMemberRole.EDITOR)
