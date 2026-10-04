"""TripPreferences repository."""
from __future__ import annotations

import uuid
from typing import Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models.trip import TripPreferences


class TripPreferencesRepository:
    def __init__(self, db: AsyncSession):
        self.db = db

    async def get(self, trip_id: uuid.UUID) -> Optional[TripPreferences]:
        result = await self.db.execute(select(TripPreferences).where(TripPreferences.trip_id == trip_id))
        return result.scalar_one_or_none()

    async def create(self, prefs: TripPreferences) -> TripPreferences:
        self.db.add(prefs)
        await self.db.flush()
        return prefs

    async def save(self, prefs: TripPreferences) -> TripPreferences:
        await self.db.flush()
        return prefs
