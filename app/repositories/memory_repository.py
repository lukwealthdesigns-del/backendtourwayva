"""UserMemory repository."""
from __future__ import annotations

import uuid
from typing import Optional, Sequence

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models.memory import UserMemory


class MemoryRepository:
    def __init__(self, db: AsyncSession):
        self.db = db

    async def create(self, memory: UserMemory) -> UserMemory:
        self.db.add(memory)
        await self.db.flush()
        return memory

    async def get_by_id(self, memory_id: uuid.UUID) -> Optional[UserMemory]:
        result = await self.db.execute(select(UserMemory).where(UserMemory.id == memory_id))
        return result.scalar_one_or_none()

    async def list_for_user(self, user_id: uuid.UUID, *, enabled_only: bool = False) -> Sequence[UserMemory]:
        stmt = select(UserMemory).where(UserMemory.user_id == user_id)
        if enabled_only:
            stmt = stmt.where(UserMemory.is_enabled.is_(True))
        stmt = stmt.order_by(UserMemory.created_at.desc())
        result = await self.db.execute(stmt)
        return result.scalars().all()

    async def save(self, memory: UserMemory) -> UserMemory:
        await self.db.flush()
        return memory

    async def delete(self, memory: UserMemory) -> None:
        await self.db.delete(memory)
        await self.db.flush()
