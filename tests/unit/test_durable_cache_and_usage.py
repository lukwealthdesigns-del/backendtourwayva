"""Durable geocoding/currency caches (Redis-restart survival), usage repositories, and the
RLS session-variable setter — all with fake sessions/repos, no real database."""
from __future__ import annotations

import asyncio
import uuid
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace as NS

import pytest

from app.core.config import settings
from app.services import durable_cache


def _run(coro):
    return asyncio.run(coro)


class _FakeDB:
    committed = False

    async def commit(self):
        self.committed = True

    async def execute(self, *a, **k):
        return NS(scalar_one_or_none=lambda: None)

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False


class _SessionFactory:
    """Stands in for AsyncSessionLocal — returns the same fake session each call."""

    def __init__(self, session):
        self._session = session

    def __call__(self):
        return self._session


# ---------------------------------------------------------------------------
# durable_cache: geocoding
# ---------------------------------------------------------------------------
def test_geocode_round_trips_through_the_durable_repository(monkeypatch):
    stored = {}

    class Repo:
        def __init__(self, db):
            pass

        async def get_geocode(self, cache_key, *, now):
            entry = stored.get(cache_key)
            return entry["response"] if entry and entry["expires_at"] > now else None

        async def set_geocode(self, cache_key, *, kind, response, expires_at):
            stored[cache_key] = {"response": response, "expires_at": expires_at}

    monkeypatch.setattr("app.repositories.provider_cache_repository.ProviderCacheRepository", Repo)
    monkeypatch.setattr("app.db.session.AsyncSessionLocal", _SessionFactory(_FakeDB()))

    assert _run(durable_cache.get_geocode("paris")) is None
    _run(durable_cache.set_geocode("paris", kind="forward", response={"lat": 48.8}, ttl_seconds=60))
    assert _run(durable_cache.get_geocode("paris")) == {"lat": 48.8}


def test_an_expired_durable_geocode_entry_is_not_returned(monkeypatch):
    stored = {"paris": {"response": {"lat": 48.8}, "expires_at": datetime.now(timezone.utc) - timedelta(seconds=1)}}

    class Repo:
        def __init__(self, db):
            pass

        async def get_geocode(self, cache_key, *, now):
            entry = stored.get(cache_key)
            return entry["response"] if entry and entry["expires_at"] > now else None

    monkeypatch.setattr("app.repositories.provider_cache_repository.ProviderCacheRepository", Repo)
    monkeypatch.setattr("app.db.session.AsyncSessionLocal", _SessionFactory(_FakeDB()))
    assert _run(durable_cache.get_geocode("paris")) is None


def test_a_database_outage_never_breaks_a_geocode_lookup_it_just_returns_none(monkeypatch):
    class BrokenFactory:
        def __call__(self):
            raise ConnectionError("db down")

    monkeypatch.setattr("app.db.session.AsyncSessionLocal", BrokenFactory())
    assert _run(durable_cache.get_geocode("paris")) is None
    _run(durable_cache.set_geocode("paris", kind="forward", response={}, ttl_seconds=60))   # must not raise


# ---------------------------------------------------------------------------
# durable_cache: currency
# ---------------------------------------------------------------------------
def test_currency_round_trips_and_reports_its_own_provider_and_fetch_time(monkeypatch):
    stored = {}
    fetched = datetime(2026, 9, 20, tzinfo=timezone.utc)

    class Repo:
        def __init__(self, db):
            pass

        async def get_currency_rate(self, base, target, *, now):
            row = stored.get((base, target))
            if row is None or row.expires_at <= now:
                return None
            return row

        async def set_currency_rate(self, *, base, target, rate, provider, fetched_at, expires_at):
            stored[(base, target)] = NS(base=base, target=target, rate=rate, provider=provider, fetched_at=fetched_at,
                                        expires_at=expires_at)

    monkeypatch.setattr("app.repositories.provider_cache_repository.ProviderCacheRepository", Repo)
    monkeypatch.setattr("app.db.session.AsyncSessionLocal", _SessionFactory(_FakeDB()))

    assert _run(durable_cache.get_currency_rate("USD", "NGN")) is None
    _run(durable_cache.set_currency_rate(base="USD", target="NGN", rate=1500.0, provider="currencyapi",
                                         fetched_at=fetched, ttl_seconds=3600))
    result = _run(durable_cache.get_currency_rate("USD", "NGN"))
    assert result == {"base": "USD", "target": "NGN", "rate": 1500.0, "provider": "currencyapi", "fetched_at": fetched}


# ---------------------------------------------------------------------------
# CurrencyService / GeocodingService: the three-layer fallback
# ---------------------------------------------------------------------------
def test_currency_service_falls_back_to_the_durable_cache_before_calling_the_live_provider(monkeypatch):
    from app.modules.currency import service as currency_module

    calls = {"redis_get": 0, "redis_set": [], "durable_get": 0, "durable_set": 0, "live": 0}

    async def redis_get(key):
        calls["redis_get"] += 1
        return None

    async def redis_set(key, value, ttl):
        calls["redis_set"].append((key, ttl))

    async def durable_get(base, target):
        calls["durable_get"] += 1
        return {"base": "USD", "target": "NGN", "rate": 1450.0, "provider": "currencyapi",
                "fetched_at": datetime(2026, 9, 19, tzinfo=timezone.utc)}

    async def durable_set(**kwargs):
        calls["durable_set"] += 1

    class DeadProvider:
        async def get_latest_rate(self, base, target):
            calls["live"] += 1
            raise AssertionError("must not call the live provider when the durable cache has a fresh rate")

    monkeypatch.setattr(currency_module.CacheService, "get_json", staticmethod(redis_get))
    monkeypatch.setattr(currency_module.CacheService, "set_json", staticmethod(redis_set))
    monkeypatch.setattr(currency_module.durable_cache, "get_currency_rate", durable_get)
    monkeypatch.setattr(currency_module.durable_cache, "set_currency_rate", durable_set)
    monkeypatch.setattr(currency_module, "_provider", DeadProvider())

    response = _run(currency_module.CurrencyService().get_rate("usd", "ngn"))

    assert response.rate == 1450.0 and response.source == "cache" and calls["live"] == 0
    assert calls["durable_get"] == 1 and calls["redis_set"]              # promoted back into Redis
    assert calls["durable_set"] == 0                                     # a durable HIT is not re-written


def test_currency_service_writes_through_to_both_caches_on_a_live_call(monkeypatch):
    from app.modules.currency import service as currency_module

    written = {"redis": None, "durable": None}

    async def redis_get(key):
        return None

    async def redis_set(key, value, ttl):
        written["redis"] = (key, value, ttl)

    async def durable_get(base, target):
        return None

    async def durable_set(**kwargs):
        written["durable"] = kwargs

    class LiveProvider:
        async def get_latest_rate(self, base, target):
            return NS(base=base, target=target, rate=1600.0, fetched_at=datetime.now(timezone.utc), provider="currencyapi")

    monkeypatch.setattr(currency_module.CacheService, "get_json", staticmethod(redis_get))
    monkeypatch.setattr(currency_module.CacheService, "set_json", staticmethod(redis_set))
    monkeypatch.setattr(currency_module.durable_cache, "get_currency_rate", durable_get)
    monkeypatch.setattr(currency_module.durable_cache, "set_currency_rate", durable_set)
    monkeypatch.setattr(currency_module, "_provider", LiveProvider())

    response = _run(currency_module.CurrencyService().get_rate("USD", "NGN"))

    assert response.source == "live" and response.rate == 1600.0
    assert written["redis"] is not None and written["durable"]["rate"] == 1600.0


def test_geocoding_service_falls_back_to_the_durable_cache_before_the_live_provider(monkeypatch):
    from app.modules.geocoding import service as geocoding_module

    async def redis_get(key):
        return None

    async def redis_set(key, value, ttl):
        return None

    async def durable_get(key):
        return {"formatted_address": "Paris, France", "latitude": 48.85, "longitude": 2.35, "country": "France",
                "city": "Paris", "region": "Ile-de-France", "provider": "opencage"}

    class DeadProvider:
        async def forward_geocode(self, query):
            raise AssertionError("must not call the live provider")

    monkeypatch.setattr(geocoding_module.CacheService, "get_json", staticmethod(redis_get))
    monkeypatch.setattr(geocoding_module.CacheService, "set_json", staticmethod(redis_set))
    monkeypatch.setattr(geocoding_module.durable_cache, "get_geocode", durable_get)
    monkeypatch.setattr(geocoding_module, "_provider", DeadProvider())

    response = _run(geocoding_module.GeocodingService().forward_geocode("Paris"))
    assert response.source == "cache" and response.formatted_address == "Paris, France"


# ---------------------------------------------------------------------------
# Usage repositories
# ---------------------------------------------------------------------------
def test_provider_usage_repository_records_a_row():
    from app.repositories.provider_usage_repository import ProviderUsageRepository

    class Db:
        def __init__(self):
            self.added = []

        def add(self, row):
            self.added.append(row)

        async def flush(self):
            pass

    db = Db()
    _run(ProviderUsageRepository(db).record_provider_call(
        provider="weatherapi", success=False, duration_ms=1234.5, circuit_state="open", error_type="TimeoutException"
    ))
    (row,) = db.added
    assert (row.provider, row.success, row.circuit_state, row.error_type) == ("weatherapi", False, "open", "TimeoutException")


def test_api_usage_repository_records_a_row_with_a_nullable_user():
    from app.repositories.provider_usage_repository import ProviderUsageRepository

    class Db:
        def __init__(self):
            self.added = []

        def add(self, row):
            self.added.append(row)

        async def flush(self):
            pass

    db = Db()
    _run(ProviderUsageRepository(db).record_api_call(user_id=None, method="GET", path="/health", status_code=200, duration_ms=3.1))
    (row,) = db.added
    assert row.user_id is None and row.path == "/health" and row.status_code == 200


# ---------------------------------------------------------------------------
# RLS session variable
# ---------------------------------------------------------------------------
def test_set_rls_user_is_a_no_op_when_enforcement_is_off(monkeypatch):
    from app.db.rls import set_rls_user

    monkeypatch.setattr(settings, "RLS_ENFORCE", False)
    calls = []

    class Db:
        async def execute(self, *a, **k):
            calls.append((a, k))

    _run(set_rls_user(Db(), uuid.uuid4()))
    assert calls == []


def test_set_rls_user_sets_the_session_variable_when_enforced(monkeypatch):
    from app.db.rls import set_rls_user

    monkeypatch.setattr(settings, "RLS_ENFORCE", True)
    calls = []

    class Db:
        async def execute(self, statement, params=None):
            calls.append((statement, params))

    user_id = uuid.uuid4()
    _run(set_rls_user(Db(), user_id))
    assert len(calls) == 1 and calls[0][1] == {"uid": str(user_id)}


def test_set_rls_user_never_raises_even_if_the_database_call_fails(monkeypatch):
    from app.db.rls import set_rls_user

    monkeypatch.setattr(settings, "RLS_ENFORCE", True)

    class Db:
        async def execute(self, *a, **k):
            raise ConnectionError("db down")

    _run(set_rls_user(Db(), uuid.uuid4()))   # must not raise
