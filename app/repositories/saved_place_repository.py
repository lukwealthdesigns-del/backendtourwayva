"""Saved places: create/list/get/delete, scoped to the owning user."""
from __future__ import annotations

import uuid
from typing import Optional, Sequence

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models.saved_place import SavedPlace


class SavedPlaceRepository:
    def __init__(self, db: AsyncSession):
        self.db = db

    async def create(self, place: SavedPlace) -> SavedPlace:
        self.db.add(place)
        await self.db.flush()
        return place

    async def get(self, place_id: uuid.UUID) -> Optional[SavedPlace]:
        result = await self.db.execute(select(SavedPlace).where(SavedPlace.id == place_id))
        return result.scalar_one_or_none()

    async def list_for_user(self, user_id: uuid.UUID, *, trip_id: Optional[uuid.UUID] = None) -> Sequence[SavedPlace]:
        stmt = select(SavedPlace).where(SavedPlace.user_id == user_id)
        if trip_id is not None:
            stmt = stmt.where(SavedPlace.trip_id == trip_id)
        result = await self.db.execute(stmt.order_by(SavedPlace.created_at.desc()))
        return result.scalars().all()

    async def delete(self, place: SavedPlace) -> None:
        await self.db.delete(place)
        await self.db.flush()

    async def find_duplicate(self, user_id: uuid.UUID, *, name: str, latitude: float, longitude: float) -> Optional[SavedPlace]:
        """Loose de-dup: same user, same name, coordinates within ~100m (0.001 degrees)."""
        result = await self.db.execute(
            select(SavedPlace).where(
                SavedPlace.user_id == user_id, SavedPlace.name == name,
                SavedPlace.latitude.between(latitude - 0.001, latitude + 0.001),
                SavedPlace.longitude.between(longitude - 0.001, longitude + 0.001),
            )
        )
        return result.scalars().first()
