"""Location autocomplete: place-level filtering/dedup of provider results, the default provider fallback, and the
cached service path."""
from __future__ import annotations

import asyncio

from app.core.exceptions import NotFoundError
from app.modules.geocoding import service as geocoding_service
from app.modules.geocoding.service import GeocodingService
from app.providers.geocoding.interface import GeocodeResult, GeocodingProvider
from app.providers.geocoding.opencage_provider import parse_suggestions


def _run(coro):
    return asyncio.run(coro)


def _item(formatted, kind, lat=1.0, lng=2.0, **components):
    return {"formatted": formatted, "geometry": {"lat": lat, "lng": lng}, "confidence": 7,
            "components": {"_type": kind, **components}}


def test_parse_keeps_places_and_drops_roads_and_buildings():
    raw = [
        _item("Lagos, Nigeria", "city", city="Lagos", country_code="ng", state="Lagos"),
        _item("Lagos Street 4, Porto", "road", country_code="pt"),
        _item("Lagos, Algarve, Portugal", "town", town="Lagos", country_code="pt", state="Faro"),
        _item("Lagos Plaza", "building", country_code="ng"),
    ]
    out = parse_suggestions(raw, limit=6)
    assert [r.formatted_address for r in out] == ["Lagos, Nigeria", "Lagos, Algarve, Portugal"]
    assert out[0].country == "NG" and out[0].city == "Lagos" and out[1].city == "Lagos"


def test_parse_dedupes_and_respects_limit_and_skips_missing_coordinates():
    raw = [_item("Paris, France", "city"), _item("paris, france", "city"), _item("Paris, Texas, USA", "city"),
           {"formatted": "Parisx", "geometry": {}, "components": {"_type": "city"}}, _item("Paris, Idaho, USA", "city")]
    out = parse_suggestions(raw, limit=2)
    assert [r.formatted_address for r in out] == ["Paris, France", "Paris, Texas, USA"]


class _SingleProvider(GeocodingProvider):
    def __init__(self, result=None):
        self.result = result

    async def forward_geocode(self, query):
        if self.result is None:
            raise NotFoundError("none")
        return self.result

    async def reverse_geocode(self, latitude, longitude):  # pragma: no cover - unused
        raise NotImplementedError


def test_default_search_places_falls_back_to_single_best_match():
    r = GeocodeResult("Accra, Ghana", 5.6, -0.2, "GH", "Accra", "Greater Accra", "mock")
    assert _run(_SingleProvider(r).search_places("accra")) == [r]
    assert _run(_SingleProvider(None).search_places("zzzz")) == []


def test_service_suggest_caches_by_normalized_query(monkeypatch):
    store: dict = {}
    calls = {"n": 0}

    class FakeCache:
        @staticmethod
        async def get_json(key):
            return store.get(key)

        @staticmethod
        async def set_json(key, value, ttl):
            store[key] = value

    class FakeProvider:
        async def search_places(self, query, limit):
            calls["n"] += 1
            return [GeocodeResult("Lagos, Nigeria", 6.5, 3.4, "NG", "Lagos", "Lagos", "fake")]

    monkeypatch.setattr(geocoding_service, "CacheService", FakeCache)
    monkeypatch.setattr(geocoding_service, "_provider", FakeProvider())
    svc = GeocodingService()
    first = _run(svc.suggest("  Lagos ", 6))
    second = _run(svc.suggest("lagos", 6))
    assert calls["n"] == 1
    assert first.source == "live" and second.source == "cache"
    assert second.results[0].formatted_address == "Lagos, Nigeria" and second.results[0].country == "NG"
