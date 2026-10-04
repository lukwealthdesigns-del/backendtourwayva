"""Repository for visited destinations and visited places."""
from __future__ import annotations

import uuid
from typing import Optional, Sequence

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models.travel_history import VisitedDestination, VisitedPlace


class TravelHistoryRepository:
    def __init__(self, db: AsyncSession):
        self.db = db

    async def create_destination(self, destination: VisitedDestination) -> VisitedDestination:
        self.db.add(destination)
        await self.db.flush()
        return destination

    async def list_destinations_for_user(self, user_id: uuid.UUID) -> Sequence[VisitedDestination]:
        result = await self.db.execute(
            select(VisitedDestination)
            .where(VisitedDestination.user_id == user_id)
            .order_by(VisitedDestination.end_date.desc())
        )
        return result.scalars().all()

    async def find_last_trip_to(self, user_id: uuid.UUID, destination_query: str) -> Optional[VisitedDestination]:
        result = await self.db.execute(
            select(VisitedDestination)
            .where(VisitedDestination.user_id == user_id, VisitedDestination.destination.ilike(f"%{destination_query}%"))
            .order_by(VisitedDestination.end_date.desc())
            .limit(1)
        )
        return result.scalar_one_or_none()

    async def create_place(self, place: VisitedPlace) -> VisitedPlace:
        self.db.add(place)
        await self.db.flush()
        return place

    async def list_places_for_trip_and_user(self, user_id: uuid.UUID, trip_id: uuid.UUID) -> Sequence[VisitedPlace]:
        result = await self.db.execute(
            select(VisitedPlace).where(VisitedPlace.user_id == user_id, VisitedPlace.trip_id == trip_id)
        )
        return result.scalars().all()

    async def list_all_places_for_user(self, user_id: uuid.UUID) -> Sequence[VisitedPlace]:
        result = await self.db.execute(
            select(VisitedPlace).where(VisitedPlace.user_id == user_id).order_by(VisitedPlace.visited_date.desc())
        )
        return result.scalars().all()
