"""
Rate limiting and abuse prevention (Master Blueprint §62-63).

Fixed-window counter backed by Redis: `INCR` a key scoped to
(bucket, window) and set its expiry on first increment. Simple,
Redis-native, and correct enough for this delivery — a sliding-window
or token-bucket algorithm would smooth out edge-of-window bursts, but
fixed-window is the standard starting point and is what most rate
limiters ship with first.

Like CacheService, this NEVER raises on Redis unavailability for the
check itself — if Redis is down, rate limiting fails open (allows the
request) rather than taking the whole API down over a
non-load-bearing dependency. Redis being unreachable is a
degraded-mode condition, not a reason to 500 every request.
"""
from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Optional

import redis.asyncio as aioredis

from app.core.config import settings
from app.core.exceptions import RateLimitedError
from app.core.logging import get_logger

logger = get_logger(__name__)

_redis_client: Optional[aioredis.Redis] = None


def _get_client() -> aioredis.Redis:
    global _redis_client
    if _redis_client is None:
        _redis_client = aioredis.from_url(settings.REDIS_URL, decode_responses=True)
    return _redis_client


@dataclass(frozen=True)
class RateLimitResult:
    allowed: bool
    remaining: int
    retry_after_seconds: int


async def check_rate_limit(*, key: str, max_requests: int, window_seconds: int) -> RateLimitResult:
    """Increments the counter for `key` in the current fixed window
    and reports whether the caller is still within `max_requests`.
    Fails open (allowed=True) if Redis is unreachable."""
    window_id = int(time.time()) // window_seconds
    redis_key = f"ratelimit:{key}:{window_id}"

    try:
        client = _get_client()
        count = await client.incr(redis_key)
        if count == 1:
            await client.expire(redis_key, window_seconds)
    except Exception as exc:  # noqa: BLE001
        logger.warning("rate_limit_check_failed_open", key=key, error=str(exc))
        return RateLimitResult(allowed=True, remaining=max_requests, retry_after_seconds=0)

    if count > max_requests:
        retry_after = window_seconds - (int(time.time()) % window_seconds)
        return RateLimitResult(allowed=False, remaining=0, retry_after_seconds=retry_after)

    return RateLimitResult(allowed=True, remaining=max_requests - count, retry_after_seconds=0)


async def enforce_rate_limit(*, key: str, max_requests: int, window_seconds: int) -> None:
    """Same as check_rate_limit but raises RateLimitedError (429)
    directly — the usual call for a FastAPI dependency."""
    result = await check_rate_limit(key=key, max_requests=max_requests, window_seconds=window_seconds)
    if not result.allowed:
        raise RateLimitedError(
            "Too many requests. Please slow down and try again shortly.",
            details={"retry_after_seconds": result.retry_after_seconds},
        )


# --- Failed-login abuse prevention (Blueprint §63: "repeated failed login") ---

_FAILED_LOGIN_WINDOW_SECONDS = 15 * 60
_FAILED_LOGIN_MAX_ATTEMPTS = 5


def _failed_login_key(email: str) -> str:
    return f"failed_login:{email.strip().lower()}"


async def record_failed_login(email: str) -> None:
    try:
        client = _get_client()
        key = _failed_login_key(email)
        count = await client.incr(key)
        if count == 1:
            await client.expire(key, _FAILED_LOGIN_WINDOW_SECONDS)
    except Exception as exc:  # noqa: BLE001
        logger.warning("failed_login_tracking_failed", error=str(exc))


async def clear_failed_logins(email: str) -> None:
    try:
        client = _get_client()
        await client.delete(_failed_login_key(email))
    except Exception as exc:  # noqa: BLE001
        logger.warning("failed_login_clear_failed", error=str(exc))


async def is_login_locked_out(email: str) -> bool:
    try:
        client = _get_client()
        raw = await client.get(_failed_login_key(email))
        return raw is not None and int(raw) >= _FAILED_LOGIN_MAX_ATTEMPTS
    except Exception as exc:  # noqa: BLE001
        logger.warning("failed_login_check_failed_open", error=str(exc))
        return False
