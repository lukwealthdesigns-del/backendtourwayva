"""Destination guides for any place: output is validated/clamped, the place must exist, and results are cached."""
from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace

import pytest

from app.core.exceptions import NotFoundError, ProviderUnavailableError
from app.modules.destinations import service as ds

GOOD = {
    "overview": "A hilly coastal capital of tiled streets and trams.", "best_time": "Mar-Jun, Sep-Oct", "suggested_days": "3-4",
    "daily_budget_usd": 120, "highlights": ["Belém Tower", "Alfama", "Tram 28", "Sintra day trip"],
    "languages": "Portuguese", "currency_code": "eur", "tips": ["Wear flat shoes", "Book Sintra early", "third tip is dropped"],
}


def test_parse_accepts_a_complete_guide_and_normalises_it():
    out = ds.parse_guide(json.dumps(GOOD))
    assert out["currency_code"] == "EUR" and out["daily_budget_usd"] == 120
    assert len(out["highlights"]) == 4 and len(out["tips"]) == 2


def test_parse_clamps_numbers_and_lengths_and_dedupes():
    out = ds.parse_guide(json.dumps({**GOOD, "daily_budget_usd": 999999, "overview": "x" * 900, "highlights": ["A", "A", "B", "C", "D"]}))
    assert out["daily_budget_usd"] == 2000 and len(out["overview"]) == 420 and out["highlights"] == ["A", "B", "C", "D"]
    assert ds.parse_guide(json.dumps({**GOOD, "daily_budget_usd": 1}))["daily_budget_usd"] == 10


@pytest.mark.parametrize("patch", [
    {"overview": ""}, {"best_time": None}, {"suggested_days": 3}, {"daily_budget_usd": "cheap"}, {"daily_budget_usd": True},
    {"highlights": ["only", "two"]}, {"highlights": "Eiffel"},
])
def test_incomplete_or_wrongly_typed_output_is_rejected(patch):
    with pytest.raises(ValueError):
        ds.parse_guide(json.dumps({**GOOD, **patch}))


def test_bad_currency_codes_are_dropped_not_passed_through():
    assert ds.parse_guide(json.dumps({**GOOD, "currency_code": "EURO"}))["currency_code"] is None
    assert ds.parse_guide(json.dumps({**GOOD, "currency_code": "<b>"}))["currency_code"] is None


class _Geo:
    def __init__(self, found=True):
        self.found = found

    async def forward_geocode(self, query):
        if not self.found:
            raise NotFoundError("none")
        return SimpleNamespace(formatted_address="Lisbon, Portugal", latitude=38.7, longitude=-9.1, country="PT", city="Lisbon", region=None)


class _Llm:
    def __init__(self, content):
        self.content, self.calls = content, 0

    async def generate(self, messages, **kw):
        self.calls += 1
        return SimpleNamespace(content=self.content)


@pytest.fixture
def cache(monkeypatch):
    store = {}

    class C:
        @staticmethod
        async def get_json(k):
            return store.get(k)

        @staticmethod
        async def set_json(k, v, ttl):
            store[k] = v

    monkeypatch.setattr(ds, "CacheService", C)
    return store


def test_unknown_place_is_a_404_and_the_model_is_never_asked(cache):
    llm = _Llm(json.dumps(GOOD))
    with pytest.raises(NotFoundError):
        asyncio.run(ds.DestinationService(llm=llm, geocoder=_Geo(False)).guide(name="Asdfgh", country=None))
    assert llm.calls == 0


def test_guide_is_built_for_the_resolved_place_and_cached(cache):
    llm = _Llm(json.dumps(GOOD))
    svc = ds.DestinationService(llm=llm, geocoder=_Geo())
    first = asyncio.run(svc.guide(name="lisbon", country="Portugal"))
    second = asyncio.run(svc.guide(name="Lisbon", country="Portugal"))
    assert first.country_code == "PT" and first.source == "ai" and first.cached is False
    assert second.cached is True and llm.calls == 1


def test_unusable_model_output_is_a_retryable_503_and_is_not_cached(cache):
    svc = ds.DestinationService(llm=_Llm("sorry, I can't"), geocoder=_Geo())
    with pytest.raises(ProviderUnavailableError):
        asyncio.run(svc.guide(name="Lisbon", country=None))
    assert cache == {}
