"""OverpassOpeningHoursProvider (app/providers/opening_hours/overpass_provider.py).

No network: httpx.AsyncClient and CacheService are faked. Covers the
contract that matters most — every failure path returns None ("unknown"),
never raises, and a real match is cached so a second lookup for the same
venue does not hit Overpass (or the shared circuit breaker) again.
"""
from __future__ import annotations

import asyncio
from types import SimpleNamespace as NS

import httpx
import pytest

from app.core.resilience import reset_breakers
from app.providers.opening_hours.factory import get_opening_hours_provider
from app.providers.opening_hours.mock_provider import MockOpeningHoursProvider
from app.providers.opening_hours.overpass_provider import OverpassOpeningHoursProvider
import app.providers.opening_hours.overpass_provider as overpass_module


def _run(coro):
    return asyncio.run(coro)


@pytest.fixture(autouse=True)
def _isolated(monkeypatch):
    reset_breakers()
    store: dict[str, object] = {}

    async def get_json(key):
        return store.get(key)

    async def set_json(key, value, ttl):
        store[key] = value

    monkeypatch.setattr(overpass_module.CacheService, "get_json", staticmethod(get_json))
    monkeypatch.setattr(overpass_module.CacheService, "set_json", staticmethod(set_json))
    yield store
    reset_breakers()


class _FakeResponse:
    def __init__(self, status_code, payload=None):
        self.status_code = status_code
        self._payload = payload or {}
        self.request = httpx.Request("POST", "https://overpass.example/api")

    def json(self):
        return self._payload


class _FakeAsyncClient:
    def __init__(self, outcome):
        self._outcome = outcome

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def post(self, url, data=None):
        self.sent = data
        if isinstance(self._outcome, Exception):
            raise self._outcome
        return self._outcome


def _patch_http(monkeypatch, outcome):
    monkeypatch.setattr(overpass_module, "httpx", NS(AsyncClient=lambda **k: _FakeAsyncClient(outcome), HTTPError=httpx.HTTPError))


ELEMENTS = {"elements": [{"tags": {"name": "Eiffel Tower", "opening_hours": "Mo-Su 09:30-23:00"}}]}


@pytest.fixture()
def provider():
    return OverpassOpeningHoursProvider(timeout=5, radius_m=75)


def test_a_matched_venue_returns_hours(monkeypatch, provider):
    _patch_http(monkeypatch, _FakeResponse(200, ELEMENTS))
    result = _run(provider.lookup(name="Eiffel Tower Summit Ticket", latitude=48.8584, longitude=2.2945))
    assert result.raw == "Mo-Su 09:30-23:00" and result.source == "openstreetmap" and result.matched_name == "Eiffel Tower"


def test_a_found_result_is_cached_so_a_second_lookup_does_not_hit_overpass(monkeypatch, provider):
    calls = {"n": 0}

    class CountingClient(_FakeAsyncClient):
        async def post(self, url, data=None):
            calls["n"] += 1
            return await super().post(url, data=data)

    monkeypatch.setattr(overpass_module, "httpx", NS(AsyncClient=lambda **k: CountingClient(_FakeResponse(200, ELEMENTS)), HTTPError=httpx.HTTPError))

    first = _run(provider.lookup(name="Eiffel Tower", latitude=48.8584, longitude=2.2945))
    second = _run(provider.lookup(name="Eiffel Tower", latitude=48.8584, longitude=2.2945))
    assert first.raw == second.raw and calls["n"] == 1


def test_no_matching_element_caches_a_miss_and_is_not_re_queried(monkeypatch, provider):
    calls = {"n": 0}

    class CountingClient(_FakeAsyncClient):
        async def post(self, url, data=None):
            calls["n"] += 1
            return await super().post(url, data=data)

    monkeypatch.setattr(overpass_module, "httpx", NS(
        AsyncClient=lambda **k: CountingClient(_FakeResponse(200, {"elements": []})), HTTPError=httpx.HTTPError))

    assert _run(provider.lookup(name="Nonexistent Place", latitude=0, longitude=0)) is None
    assert _run(provider.lookup(name="Nonexistent Place", latitude=0, longitude=0)) is None
    assert calls["n"] == 1


def test_an_empty_name_is_never_looked_up(monkeypatch, provider):
    _patch_http(monkeypatch, _FakeResponse(200, ELEMENTS))
    assert _run(provider.lookup(name="   ", latitude=0, longitude=0)) is None


def test_a_transport_error_returns_none_and_records_a_circuit_failure(monkeypatch, provider):
    _patch_http(monkeypatch, httpx.ConnectError("down"))
    assert _run(provider.lookup(name="Eiffel Tower", latitude=48.8584, longitude=2.2945)) is None


def test_an_http_error_status_returns_none_rather_than_raising(monkeypatch, provider):
    _patch_http(monkeypatch, _FakeResponse(503))
    assert _run(provider.lookup(name="Eiffel Tower", latitude=48.8584, longitude=2.2945)) is None


def test_malformed_json_returns_none(monkeypatch, provider):
    class BadJsonResponse(_FakeResponse):
        def json(self):
            raise ValueError("not json")

    _patch_http(monkeypatch, BadJsonResponse(200))
    assert _run(provider.lookup(name="Eiffel Tower", latitude=48.8584, longitude=2.2945)) is None


def test_a_failure_is_not_cached_so_it_is_retried_next_time(monkeypatch, provider, _isolated):
    _patch_http(monkeypatch, httpx.ConnectError("down"))
    _run(provider.lookup(name="Eiffel Tower", latitude=48.8584, longitude=2.2945))
    assert _isolated == {}                       # nothing written for a transport failure — unlike a genuine "no match"


def test_the_venue_name_never_enters_the_overpass_query_string(monkeypatch, provider):
    client_holder = {}

    class CapturingClient(_FakeAsyncClient):
        async def post(self, url, data=None):
            client_holder["data"] = data
            return await super().post(url, data=data)

    monkeypatch.setattr(overpass_module, "httpx", NS(
        AsyncClient=lambda **k: CapturingClient(_FakeResponse(200, ELEMENTS)), HTTPError=httpx.HTTPError))
    _run(provider.lookup(name="Robert'); DROP TABLE x; --", latitude=48.8584, longitude=2.2945))
    assert "Robert" not in client_holder["data"]["data"]


def test_an_open_circuit_short_circuits_without_a_network_call(monkeypatch, provider):
    from app.core.resilience import get_breaker

    breaker = get_breaker("overpass")
    for _ in range(breaker.failure_threshold):
        breaker.record_failure()

    called = {"n": 0}

    class ExplodingClient(_FakeAsyncClient):
        async def post(self, url, data=None):
            called["n"] += 1
            raise AssertionError("must not be called while the circuit is open")

    monkeypatch.setattr(overpass_module, "httpx", NS(AsyncClient=lambda **k: ExplodingClient(None), HTTPError=httpx.HTTPError))
    assert _run(provider.lookup(name="Eiffel Tower", latitude=48.8584, longitude=2.2945)) is None
    assert called["n"] == 0


# ---------------------------------------------------------------------------
# Factory
# ---------------------------------------------------------------------------
def test_factory_returns_overpass_by_default(monkeypatch):
    from app.core.config import settings

    monkeypatch.setattr(settings, "OPENING_HOURS_PROVIDER", "overpass")
    assert isinstance(get_opening_hours_provider(), OverpassOpeningHoursProvider)


def test_factory_returns_none_when_disabled(monkeypatch):
    from app.core.config import settings

    monkeypatch.setattr(settings, "OPENING_HOURS_PROVIDER", "none")
    assert get_opening_hours_provider() is None


def test_factory_returns_none_and_logs_for_an_unknown_value(monkeypatch):
    from app.core.config import settings

    monkeypatch.setattr(settings, "OPENING_HOURS_PROVIDER", "some-typo")
    assert get_opening_hours_provider() is None


# ---------------------------------------------------------------------------
# Mock provider (Blueprint §92)
# ---------------------------------------------------------------------------
def test_mock_provider_matches_by_normalized_name_and_records_calls():
    mock = MockOpeningHoursProvider({"Eiffel Tower": "Mo-Su 09:30-23:00"})
    result = _run(mock.lookup(name="  eiffel   tower  ", latitude=0, longitude=0))
    assert result.raw == "Mo-Su 09:30-23:00" and mock.calls == ["  eiffel   tower  "]


def test_mock_provider_returns_none_for_an_unknown_venue():
    mock = MockOpeningHoursProvider()
    assert _run(mock.lookup(name="Nowhere", latitude=0, longitude=0)) is None


def test_mock_provider_can_simulate_a_failure():
    mock = MockOpeningHoursProvider({"Eiffel Tower": "Mo-Su 09:30-23:00"})
    mock.fail = True
    assert _run(mock.lookup(name="Eiffel Tower", latitude=0, longitude=0)) is None
