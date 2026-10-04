"""Publishes each provider's circuit-breaker state to Redis (short TTL) so an admin
dashboard, or any process, can see current provider health without every process
sharing in-memory state. Best-effort: a publish failure never affects the call it
describes."""
from __future__ import annotations

from typing import Any, Optional

from app.core.logging import get_logger
from app.core.resilience import all_breakers
from app.services.cache_service import CacheService

logger = get_logger(__name__)

_KEY_PREFIX = "provider_health:"
_TTL_SECONDS = 120


async def record(name: str, ok: bool, elapsed: float, exc: Optional[BaseException], state: str) -> None:
    """Published on EVERY provider call (Redis, short TTL — 'is it healthy right now') AND
    persisted (Postgres, `provider_usage` — durable history for `GET /admin/provider-health`
    trends and incident review). Both are best-effort: a failure here never breaks the call
    it is describing, and this runs after the caller's own result/exception is already decided."""
    try:
        await CacheService.set_json(
            _KEY_PREFIX + name,
            {"provider": name, "state": state, "ok": ok, "elapsed_ms": round(elapsed * 1000, 1),
             "error": type(exc).__name__ if exc else None},
            _TTL_SECONDS,
        )
    except Exception as exc2:  # noqa: BLE001
        logger.warning("provider_health_publish_failed", provider=name, error=str(exc2))

    try:
        from app.db.session import AsyncSessionLocal
        from app.repositories.provider_usage_repository import ProviderUsageRepository

        async with AsyncSessionLocal() as db:
            await ProviderUsageRepository(db).record_provider_call(
                provider=name, success=ok, duration_ms=round(elapsed * 1000, 1), circuit_state=state,
                error_type=type(exc).__name__ if exc else None,
            )
            await db.commit()
    except Exception as exc2:  # noqa: BLE001
        logger.warning("provider_usage_persist_failed", provider=name, error=str(exc2))


async def snapshot() -> list[dict[str, Any]]:
    """Merge live in-process breaker state (authoritative for THIS process) with what
    is in Redis (other processes) — Redis entries not matched by a local breaker are
    included as-is."""
    result: dict[str, dict[str, Any]] = {}
    for name, breaker in all_breakers().items():
        result[name] = {"provider": name, "state": breaker.state, "consecutive_failures": breaker.consecutive_failures,
                        "retry_in_seconds": round(breaker.retry_in(), 1)}
    try:
        keys = await CacheService.list_keys(_KEY_PREFIX + "*")
        for key in keys:
            cached = await CacheService.get_json(key)
            if cached and cached["provider"] not in result:
                result[cached["provider"]] = {"provider": cached["provider"], "state": cached["state"]}
    except Exception as exc:  # noqa: BLE001
        logger.warning("provider_health_snapshot_failed", error=str(exc))
    return sorted(result.values(), key=lambda r: r["provider"])
