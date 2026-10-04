"""
MemoryService - implements the user controls Blueprint section 37
requires (view/edit/delete/disable), scoped strictly to the
authenticated user's own memories (Principle 5: privacy boundaries -
never expose or let someone mutate another user's memory).

Automatic extraction from Companion conversations IS implemented now
(see create_extracted below and app/modules/companion/
memory_extraction.py) - memories can come from two sources:
  - MemorySource.USER_STATED - via POST /memory, by the person
    themselves.
  - MemorySource.COMPANION_EXTRACTED - proposed automatically by
    Companion after a conversation turn, with a confidence score
    attached, deduped against existing memories before being stored.
Either way, the same view/edit/delete/disable controls below apply
uniformly - the person has full control regardless of source.
"""
from __future__ import annotations

import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.constants import MemorySource
from app.core.exceptions import NotFoundError
from app.db.models.memory import UserMemory
from app.modules.memory.schemas import MemoryCreateRequest, MemoryUpdateRequest
from app.repositories.memory_repository import MemoryRepository


class MemoryService:
    def __init__(self, db: AsyncSession):
        self.db = db
        self.repo = MemoryRepository(db)

    async def create(self, *, user_id: uuid.UUID, payload: MemoryCreateRequest) -> UserMemory:
        memory = UserMemory(
            user_id=user_id, content=payload.content, source=MemorySource.USER_STATED,
            confidence=None, is_enabled=True,
        )
        await self.repo.create(memory)
        await self.db.commit()
        return memory

    async def create_extracted(
        self, *, user_id: uuid.UUID, content: str, confidence: float
    ) -> UserMemory | None:
        """Stores a Companion-extracted memory, unless a
        near-duplicate already exists (case-insensitive substring
        match against the user's current enabled memories) - a
        simple but real dedupe pass so a recurring topic doesn't
        spam the same fact into the list turn after turn. Returns
        None (and stores nothing) when skipped as a duplicate."""
        existing = await self.repo.list_for_user(user_id, enabled_only=True)
        normalized_new = content.strip().lower()
        for memory in existing:
            normalized_existing = memory.content.strip().lower()
            if normalized_new in normalized_existing or normalized_existing in normalized_new:
                return None

        memory = UserMemory(
            user_id=user_id, content=content, source=MemorySource.COMPANION_EXTRACTED,
            confidence=confidence, is_enabled=True,
        )
        await self.repo.create(memory)
        await self.db.commit()
        return memory

    async def list_for_user(self, user_id: uuid.UUID):
        return await self.repo.list_for_user(user_id)

    async def list_enabled_for_user(self, user_id: uuid.UUID):
        """Used by Companion to build LLM context - disabled memories
        are never surfaced anywhere (Blueprint section 37: users can disable)."""
        return await self.repo.list_for_user(user_id, enabled_only=True)

    async def get_owned(self, *, memory_id: uuid.UUID, user_id: uuid.UUID) -> UserMemory:
        memory = await self.repo.get_by_id(memory_id)
        if memory is None or memory.user_id != user_id:
            raise NotFoundError("Memory not found.")
        return memory

    async def update(self, *, memory_id: uuid.UUID, user_id: uuid.UUID, payload: MemoryUpdateRequest) -> UserMemory:
        memory = await self.get_owned(memory_id=memory_id, user_id=user_id)
        update_data = payload.model_dump(exclude_unset=True)
        for field, value in update_data.items():
            setattr(memory, field, value)
        await self.repo.save(memory)
        await self.db.commit()
        return memory

    async def delete(self, *, memory_id: uuid.UUID, user_id: uuid.UUID) -> None:
        memory = await self.get_owned(memory_id=memory_id, user_id=user_id)
        await self.repo.delete(memory)
        await self.db.commit()
