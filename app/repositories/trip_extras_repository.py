"""Repository for TripNote, TripCost, and TripRoute — the three
"trip extras" tables that had models but no behavior yet."""
from __future__ import annotations

import uuid
from typing import Optional, Sequence

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models.trip import TripCost, TripNote, TripRoute


class TripExtrasRepository:
    def __init__(self, db: AsyncSession):
        self.db = db

    # --- Notes ---
    async def create_note(self, note: TripNote) -> TripNote:
        self.db.add(note)
        await self.db.flush()
        return note

    async def get_note(self, note_id: uuid.UUID) -> Optional[TripNote]:
        result = await self.db.execute(select(TripNote).where(TripNote.id == note_id))
        return result.scalar_one_or_none()

    async def list_notes_for_trip(self, trip_id: uuid.UUID) -> Sequence[TripNote]:
        result = await self.db.execute(
            select(TripNote).where(TripNote.trip_id == trip_id).order_by(TripNote.created_at.desc())
        )
        return result.scalars().all()

    async def delete_note(self, note: TripNote) -> None:
        await self.db.delete(note)
        await self.db.flush()

    # --- Costs ---
    async def create_cost(self, cost: TripCost) -> TripCost:
        self.db.add(cost)
        await self.db.flush()
        return cost

    async def get_cost(self, cost_id: uuid.UUID) -> Optional[TripCost]:
        result = await self.db.execute(select(TripCost).where(TripCost.id == cost_id))
        return result.scalar_one_or_none()

    async def list_costs_for_trip(self, trip_id: uuid.UUID) -> Sequence[TripCost]:
        result = await self.db.execute(
            select(TripCost).where(TripCost.trip_id == trip_id).order_by(TripCost.created_at.desc())
        )
        return result.scalars().all()

    async def delete_cost(self, cost: TripCost) -> None:
        await self.db.delete(cost)
        await self.db.flush()

    # --- Routes ---
    async def get_cached_route(
        self, trip_id: uuid.UUID, origin_item_id: uuid.UUID, destination_item_id: uuid.UUID, mode: str
    ) -> Optional[TripRoute]:
        result = await self.db.execute(
            select(TripRoute).where(
                TripRoute.trip_id == trip_id,
                TripRoute.origin_item_id == origin_item_id,
                TripRoute.destination_item_id == destination_item_id,
                TripRoute.mode == mode,
            )
        )
        return result.scalar_one_or_none()

    async def create_route(self, route: TripRoute) -> TripRoute:
        self.db.add(route)
        await self.db.flush()
        return route

    async def list_routes_for_trip(self, trip_id: uuid.UUID) -> Sequence[TripRoute]:
        result = await self.db.execute(select(TripRoute).where(TripRoute.trip_id == trip_id))
        return result.scalars().all()
