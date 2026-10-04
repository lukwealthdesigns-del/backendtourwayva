"""Repository for discovery searches and results."""
from __future__ import annotations

import uuid
from typing import Sequence

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models.discover import DiscoveryResult, DiscoverySearch


class DiscoverRepository:
    def __init__(self, db: AsyncSession):
        self.db = db

    async def create_search(self, search: DiscoverySearch) -> DiscoverySearch:
        self.db.add(search)
        await self.db.flush()
        return search

    async def create_result(self, result: DiscoveryResult) -> DiscoveryResult:
        self.db.add(result)
        await self.db.flush()
        return result

    async def list_searches_for_user(self, user_id: uuid.UUID, *, limit: int = 20) -> Sequence[DiscoverySearch]:
        result = await self.db.execute(
            select(DiscoverySearch)
            .where(DiscoverySearch.user_id == user_id)
            .order_by(DiscoverySearch.created_at.desc())
            .limit(limit)
        )
        return result.scalars().all()

    async def list_results_for_search(self, search_id: uuid.UUID) -> Sequence[DiscoveryResult]:
        result = await self.db.execute(
            select(DiscoveryResult).where(DiscoveryResult.search_id == search_id).order_by(DiscoveryResult.score.desc())
        )
        return result.scalars().all()
