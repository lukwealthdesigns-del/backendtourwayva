"""Attachment repository."""
from __future__ import annotations

import uuid
from typing import Optional, Sequence

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.constants import AttachmentCategory
from app.db.models.attachment import Attachment


class AttachmentRepository:
    def __init__(self, db: AsyncSession):
        self.db = db

    async def create(self, attachment: Attachment) -> Attachment:
        self.db.add(attachment)
        await self.db.flush()
        return attachment

    async def get(self, attachment_id: uuid.UUID) -> Optional[Attachment]:
        result = await self.db.execute(select(Attachment).where(Attachment.id == attachment_id))
        return result.scalar_one_or_none()

    async def get_for_update(self, attachment_id: uuid.UUID) -> Optional[Attachment]:
        """Row-locked read: two workers handed the same attachment cannot both process it."""
        result = await self.db.execute(
            select(Attachment).where(Attachment.id == attachment_id).with_for_update()
        )
        return result.scalar_one_or_none()

    async def list_for_user(
        self, user_id: uuid.UUID, *, category: Optional[AttachmentCategory] = None
    ) -> Sequence[Attachment]:
        conditions = [Attachment.user_id == user_id]
        if category is not None:
            conditions.append(Attachment.category == category)
        result = await self.db.execute(
            select(Attachment).where(*conditions).order_by(Attachment.created_at.desc())
        )
        return result.scalars().all()

    async def list_for_trip(self, trip_id: uuid.UUID) -> Sequence[Attachment]:
        result = await self.db.execute(
            select(Attachment).where(Attachment.trip_id == trip_id).order_by(Attachment.created_at.desc())
        )
        return result.scalars().all()

    async def save(self, attachment: Attachment) -> Attachment:
        await self.db.flush()
        return attachment
