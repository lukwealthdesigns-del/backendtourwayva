"""
Durable read-through helpers for geocoding and currency lookups (Master Prompt §5).

Both `CurrencyService` and `GeocodingService` are stateless (constructed without a database
session throughout the codebase), so this module opens its own short-lived session per call —
the same pattern already used by `app/core/provider_health.py`. Every operation is best-effort:
a failure here (DB down, etc.) never blocks a lookup that Redis or the live provider can still
satisfy, and callers fall through to the next layer exactly as if this layer did not exist.

    Redis (fast) -> HERE (survives a Redis restart) -> live provider call
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Optional

from app.core.logging import get_logger

logger = get_logger(__name__)


async def get_geocode(cache_key: str) -> Optional[dict[str, Any]]:
    try:
        from app.db.session import AsyncSessionLocal
        from app.repositories.provider_cache_repository import ProviderCacheRepository

        async with AsyncSessionLocal() as db:
            return await ProviderCacheRepository(db).get_geocode(cache_key, now=datetime.now(timezone.utc))
    except Exception as exc:  # noqa: BLE001
        logger.warning("durable_geocode_read_failed", error=str(exc))
        return None


async def set_geocode(cache_key: str, *, kind: str, response: dict[str, Any], ttl_seconds: int) -> None:
    try:
        from app.db.session import AsyncSessionLocal
        from app.repositories.provider_cache_repository import ProviderCacheRepository

        expires_at = datetime.now(timezone.utc).timestamp() + ttl_seconds
        async with AsyncSessionLocal() as db:
            await ProviderCacheRepository(db).set_geocode(
                cache_key, kind=kind, response=response, expires_at=datetime.fromtimestamp(expires_at, tz=timezone.utc)
            )
    except Exception as exc:  # noqa: BLE001
        logger.warning("durable_geocode_write_failed", error=str(exc))


async def get_currency_rate(base: str, target: str) -> Optional[dict[str, Any]]:
    try:
        from app.db.session import AsyncSessionLocal
        from app.repositories.provider_cache_repository import ProviderCacheRepository

        async with AsyncSessionLocal() as db:
            row = await ProviderCacheRepository(db).get_currency_rate(base, target, now=datetime.now(timezone.utc))
        if row is None:
            return None
        return {"base": row.base, "target": row.target, "rate": row.rate, "provider": row.provider, "fetched_at": row.fetched_at}
    except Exception as exc:  # noqa: BLE001
        logger.warning("durable_currency_read_failed", error=str(exc))
        return None


async def set_currency_rate(*, base: str, target: str, rate: float, provider: str, fetched_at: datetime, ttl_seconds: int) -> None:
    try:
        from app.db.session import AsyncSessionLocal
        from app.repositories.provider_cache_repository import ProviderCacheRepository

        expires_at = datetime.now(timezone.utc).timestamp() + ttl_seconds
        async with AsyncSessionLocal() as db:
            await ProviderCacheRepository(db).set_currency_rate(
                base=base, target=target, rate=rate, provider=provider, fetched_at=fetched_at,
                expires_at=datetime.fromtimestamp(expires_at, tz=timezone.utc),
            )
    except Exception as exc:  # noqa: BLE001
        logger.warning("durable_currency_write_failed", error=str(exc))
