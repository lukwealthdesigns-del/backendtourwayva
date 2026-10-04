"""
Durable provider-response cache (Master Prompt §5: `geocoding_cache`, `currency_cache`).

Redis (via CacheService) is the fast path and the TTL-driven source of truth for freshness;
these tables exist so that (a) a Redis restart/eviction does not force every user's next
request to re-pay provider latency for data that was looked up minutes ago, and (b) there is
a durable, queryable history of past lookups. Each row still carries its own `expires_at` —
staleness is judged the same way whether the hit came from Redis or here.
"""
from __future__ import annotations

from datetime import datetime
from typing import Any, Optional

from sqlalchemy import DateTime, Float, String
from sqlalchemy.dialects.postgresql import JSON
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin, UUIDPKMixin


class GeocodeCacheEntry(UUIDPKMixin, TimestampMixin, Base):
    __tablename__ = "geocoding_cache"

    # Normalized query ("paris france") for forward lookups, or "lat,lon" (rounded) for reverse.
    cache_key: Mapped[str] = mapped_column(String(300), unique=True, index=True, nullable=False)
    kind: Mapped[str] = mapped_column(String(10), nullable=False)   # "forward" | "reverse"
    response: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class CurrencyRateCacheEntry(UUIDPKMixin, TimestampMixin, Base):
    __tablename__ = "currency_cache"

    base: Mapped[str] = mapped_column(String(3), nullable=False, index=True)
    target: Mapped[str] = mapped_column(String(3), nullable=False, index=True)
    rate: Mapped[float] = mapped_column(Float, nullable=False)
    provider: Mapped[str] = mapped_column(String(50), nullable=False)
    fetched_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
