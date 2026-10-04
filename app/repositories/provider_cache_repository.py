"""Reads/writes for the durable geocoding/currency caches (upsert semantics)."""
from __future__ import annotations

from datetime import datetime
from typing import Any, Optional

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models.provider_cache import CurrencyRateCacheEntry, GeocodeCacheEntry


class ProviderCacheRepository:
    def __init__(self, db: AsyncSession):
        self.db = db

    async def get_geocode(self, cache_key: str, *, now: datetime) -> Optional[dict[str, Any]]:
        result = await self.db.execute(
            select(GeocodeCacheEntry).where(GeocodeCacheEntry.cache_key == cache_key, GeocodeCacheEntry.expires_at > now)
        )
        row = result.scalar_one_or_none()
        return row.response if row else None

    async def set_geocode(self, cache_key: str, *, kind: str, response: dict[str, Any], expires_at: datetime) -> None:
        stmt = pg_insert(GeocodeCacheEntry).values(cache_key=cache_key, kind=kind, response=response, expires_at=expires_at)
        await self.db.execute(stmt.on_conflict_do_update(
            index_elements=["cache_key"], set_={"response": response, "expires_at": expires_at, "kind": kind}
        ))
        await self.db.commit()

    async def get_currency_rate(self, base: str, target: str, *, now: datetime) -> Optional[CurrencyRateCacheEntry]:
        result = await self.db.execute(
            select(CurrencyRateCacheEntry).where(
                CurrencyRateCacheEntry.base == base, CurrencyRateCacheEntry.target == target,
                CurrencyRateCacheEntry.expires_at > now,
            ).order_by(CurrencyRateCacheEntry.fetched_at.desc()).limit(1)
        )
        return result.scalar_one_or_none()

    async def set_currency_rate(
        self, *, base: str, target: str, rate: float, provider: str, fetched_at: datetime, expires_at: datetime
    ) -> None:
        self.db.add(CurrencyRateCacheEntry(
            base=base, target=target, rate=rate, provider=provider, fetched_at=fetched_at, expires_at=expires_at
        ))
        await self.db.commit()
