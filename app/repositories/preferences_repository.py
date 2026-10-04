"""UserPreferences repository."""
from __future__ import annotations

import uuid
from typing import Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models.preferences import UserPreferences


class PreferencesRepository:
    def __init__(self, db: AsyncSession):
        self.db = db

    async def get(self, user_id: uuid.UUID) -> Optional[UserPreferences]:
        result = await self.db.execute(select(UserPreferences).where(UserPreferences.user_id == user_id))
        return result.scalar_one_or_none()

    async def create(self, prefs: UserPreferences) -> UserPreferences:
        self.db.add(prefs)
        await self.db.flush()
        return prefs

    async def save(self, prefs: UserPreferences) -> UserPreferences:
        await self.db.flush()
        return prefs
