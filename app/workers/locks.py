"""A Redis lock for scheduled jobs, so overlapping beat ticks (or a slow run plus the next
tick, or two beat instances by mistake) never run the same job concurrently. The jobs are
also idempotent and row-locked; this only avoids wasted duplicate work."""
from __future__ import annotations

import uuid
from typing import Any, Awaitable, Callable, Optional

from app.services.cache_service import CacheService

_PREFIX = "joblock:"


async def run_exclusive(name: str, ttl_seconds: int, job: Callable[[], Awaitable[Any]]) -> Optional[Any]:
    """Run `job` unless another run of `name` holds the lock; returns None if skipped.
    The lock expires on its own (`ttl_seconds`) if the worker dies mid-run, and is released
    only by its owner."""
    token = uuid.uuid4().hex
    acquired = await CacheService.set_if_absent(_PREFIX + name, token, ttl_seconds)
    if acquired is False:
        return None                       # another run is in progress
    try:                                  # acquired, or Redis unreachable (None): run anyway — jobs are idempotent
        return await job()
    finally:
        if acquired and await CacheService.get_raw(_PREFIX + name) == token:
            await CacheService.delete(_PREFIX + name)
