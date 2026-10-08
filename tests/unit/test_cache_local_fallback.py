"""Development fallback: with Redis down, job state must still round-trip (trip generation depends on it)."""
from __future__ import annotations

import asyncio

from app.core.config import settings
from app.services import cache_service as cs
from app.services.cache_service import CacheService


class _DeadRedis:
    async def get(self, *a, **k):
        raise ConnectionError("Error 111 connecting to localhost:6379. Connection refused.")

    set = delete = get


def _run(coro):
    return asyncio.run(coro)


def _dev(monkeypatch, env="development"):
    monkeypatch.setattr(settings, "ENVIRONMENT", env, raising=False)
    monkeypatch.setattr(cs, "_get_client", lambda: _DeadRedis())
    monkeypatch.setattr(cs, "_redis_down_until", 0.0)
    cs._local_store.clear()


def test_job_state_survives_a_dead_redis_in_development(monkeypatch):
    _dev(monkeypatch)
    _run(CacheService.set_json("planning:job:1", {"status": "queued"}, 60))
    assert _run(CacheService.get_json("planning:job:1")) == {"status": "queued"}
    _run(CacheService.set_raw("planning:latest:t", "1", 60))
    assert _run(CacheService.get_raw("planning:latest:t")) == "1"


def test_locks_and_idempotency_claims_work_locally(monkeypatch):
    _dev(monkeypatch)
    assert _run(CacheService.set_if_absent("planning:lock:t", "job-a", 60)) is True
    assert _run(CacheService.set_if_absent("planning:lock:t", "job-b", 60)) is False
    _run(CacheService.delete("planning:lock:t"))
    assert _run(CacheService.set_if_absent("planning:lock:t", "job-c", 60)) is True


def test_redis_is_not_retried_on_every_call_while_paused(monkeypatch):
    _dev(monkeypatch)
    calls = []
    monkeypatch.setattr(cs, "_get_client", lambda: calls.append(1) or _DeadRedis())
    for _ in range(5):
        _run(CacheService.get_json("weather:current:1:2"))
    assert len(calls) == 1          # one failed attempt, then paused


def test_production_does_not_use_the_local_fallback(monkeypatch):
    _dev(monkeypatch, env="production")
    _run(CacheService.set_json("planning:job:2", {"status": "queued"}, 60))
    assert _run(CacheService.get_json("planning:job:2")) is None   # a miss stays a miss
    assert _run(CacheService.set_if_absent("planning:lock:x", "j", 60)) is None
