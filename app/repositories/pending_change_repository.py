"""PendingItineraryChange repository."""
from __future__ import annotations

import uuid
from typing import Optional, Sequence

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models.pending_change import PendingItineraryChange


class PendingChangeRepository:
    def __init__(self, db: AsyncSession):
        self.db = db

    async def create(self, change: PendingItineraryChange) -> PendingItineraryChange:
        self.db.add(change)
        await self.db.flush()
        return change

    async def get_by_id(self, change_id: uuid.UUID) -> Optional[PendingItineraryChange]:
        result = await self.db.execute(
            select(PendingItineraryChange).where(PendingItineraryChange.id == change_id)
        )
        return result.scalar_one_or_none()

    async def list_for_conversation(self, conversation_id: uuid.UUID) -> Sequence[PendingItineraryChange]:
        result = await self.db.execute(
            select(PendingItineraryChange)
            .where(PendingItineraryChange.conversation_id == conversation_id)
            .order_by(PendingItineraryChange.created_at.desc())
        )
        return result.scalars().all()

    async def save(self, change: PendingItineraryChange) -> PendingItineraryChange:
        await self.db.flush()
        return change
