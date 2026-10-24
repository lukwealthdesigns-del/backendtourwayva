"""GeocodingService provider chains and policy-driven caching (app/modules/geocoding/service.py). No network, no Redis, no database."""
from __future__ import annotations

import asyncio

from app.core.exceptions import NotFoundError, ProviderUnavailableError
from app.modules.geocoding import service as svc
from app.providers.geocoding.interface import GeocodeResult


def _run(coro):
    return asyncio.run(coro)


def _result(name, provider="x"):
    return GeocodeResult(f"{name}, Nigeria", 7.0, 3.0, "NG", name, "Oyo", provider)


class FakeProvider:
    """Records calls; `plan` is a list of outcomes (a list/GeocodeResult to return, an Exception to raise)."""

    def __init__(self, label, plan=None, **init):
        self.label, self.plan, self.init, self.calls = label, list(plan or []), init, []

    def _next(self, call):
        self.calls.append(call)
        outcome = self.plan.pop(0) if self.plan else _result("Lagos", self.label)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome

    async def search_places(self, query, limit=6):
        out = self._next(("suggest", query))
        return out if isinstance(out, list) else [out]

    async def forward_geocode(self, query):
        return self._next(("forward", query))

    async def reverse_geocode(self, lat, lon):
        return self._next(("reverse", lat, lon))


class Env:
    """Fakes for the caches and the two providers, with every cache write recorded."""

    def __init__(self, monkeypatch, *, mapbox_plan=None, opencage_plan=None, token="pk.t", permanent=False, opencage_key="k"):
        self.redis, self.durable, self.mapbox_instances, self.ttls = {}, {}, [], {}
        env = self
        self.opencage = FakeProvider("opencage", opencage_plan)
        self.mapbox_plan = mapbox_plan

        class FakeCache:
            @staticmethod
            async def get_json(key):
                return env.redis.get(key)

            @staticmethod
            async def set_json(key, value, ttl):
                env.redis[key] = value
                env.ttls[key] = ttl

        async def durable_get(key):
            return env.durable.get(key)

        async def durable_set(key, *, kind, response, ttl_seconds):
            env.durable[key] = response

        def make_mapbox(**init):
            inst = FakeProvider("mapbox", env.mapbox_plan, **init)
            env.mapbox_instances.append(inst)
            return inst

        s = svc.settings
        monkeypatch.setattr(svc, "CacheService", FakeCache)
        monkeypatch.setattr(svc.durable_cache, "get_geocode", durable_get)
        monkeypatch.setattr(svc.durable_cache, "set_geocode", durable_set)
        monkeypatch.setattr(svc, "_opencage", self.opencage)
        monkeypatch.setattr(svc, "MapboxGeocodingProvider", make_mapbox)
        monkeypatch.setattr(s, "MAPBOX_ACCESS_TOKEN", token)
        monkeypatch.setattr(s, "MAPBOX_GEOCODING_PERMANENT", permanent)
        monkeypatch.setattr(s, "OPENCAGE_API_KEY", opencage_key)
        monkeypatch.setattr(s, "GEOCODING_SUGGEST_PROVIDERS", "mapbox,opencage")
        monkeypatch.setattr(s, "GEOCODING_PROVIDERS", "mapbox,opencage")
        monkeypatch.setattr(s, "GEOCODING_VENUE_PROVIDERS", "opencage,mapbox")


def _names(kind):
    return [n for n, _ in svc.build_chain(kind)]


def test_chains_without_a_mapbox_token_use_opencage_only(monkeypatch):
    Env(monkeypatch, token="")
    assert _names(svc.SUGGEST) == _names(svc.FORWARD) == _names(svc.VENUE) == ["opencage"]


def test_kept_lookups_use_mapbox_only_when_permanent_geocoding_is_on(monkeypatch):
    Env(monkeypatch, permanent=False)
    assert _names(svc.SUGGEST) == ["mapbox", "opencage"]          # autocomplete is display-only: allowed
    assert _names(svc.FORWARD) == ["opencage"]                    # kept answers must not come from temporary geocoding
    assert _names(svc.VENUE) == ["opencage"]
    Env(monkeypatch, permanent=True)
    assert _names(svc.FORWARD) == ["mapbox", "opencage"]
    assert _names(svc.VENUE) == ["opencage", "mapbox"]            # venues: OpenStreetMap POIs first


def test_the_venue_chain_builds_mapbox_in_precise_mode_and_permanent_follows_the_setting(monkeypatch):
    env = Env(monkeypatch, permanent=True)
    svc.build_chain(svc.VENUE)
    svc.build_chain(svc.FORWARD)
    assert env.mapbox_instances[0].init == {"permanent": True, "precise": True}
    assert env.mapbox_instances[1].init == {"permanent": True, "precise": False}


def test_mapbox_autocomplete_is_returned_but_never_cached(monkeypatch):
    env = Env(monkeypatch, mapbox_plan=[[_result("Ibadan", "mapbox")]])
    out = _run(svc.GeocodingService().suggest("ibadan", 6))
    assert out.source == "live" and out.provider == "mapbox" and out.results[0].formatted_address == "Ibadan, Nigeria"
    assert env.redis == {}
    _run(svc.GeocodingService().suggest("ibadan", 6))
    assert sum(len(i.calls) for i in env.mapbox_instances) == 2     # asked again: Mapbox results cannot be reused


def test_autocomplete_falls_back_when_mapbox_fails_and_that_answer_is_cached(monkeypatch):
    env = Env(monkeypatch, mapbox_plan=[ProviderUnavailableError("down")], opencage_plan=[[_result("Lagos", "opencage")]])
    out = _run(svc.GeocodingService().suggest("lagos", 6))
    assert out.provider == "opencage" and env.redis       # OpenCage data may be cached
    assert list(env.ttls.values()) == [svc.TTL_GEOCODING_SECONDS]
    again = _run(svc.GeocodingService().suggest("lagos", 6))
    assert again.source == "cache" and len(env.opencage.calls) == 1


def test_autocomplete_tries_the_next_provider_when_mapbox_finds_nothing_for_a_settled_query_only(monkeypatch):
    env = Env(monkeypatch, mapbox_plan=[[]], opencage_plan=[[_result("Abeokuta", "opencage")]])
    assert _run(svc.GeocodingService().suggest("abeokuta", 6)).provider == "opencage"
    env2 = Env(monkeypatch, mapbox_plan=[[]])
    out = _run(svc.GeocodingService().suggest("ab", 6))
    assert out.results == [] and env2.opencage.calls == []        # two characters: an empty list is the answer


def test_autocomplete_reports_an_outage_only_when_every_provider_failed(monkeypatch):
    Env(monkeypatch, mapbox_plan=[ProviderUnavailableError("a")], opencage_plan=[ProviderUnavailableError("b")])
    try:
        _run(svc.GeocodingService().suggest("lagos", 6))
    except ProviderUnavailableError:
        return
    raise AssertionError("expected an outage")


def test_forward_geocode_falls_through_not_found_and_outages_in_order(monkeypatch):
    env = Env(monkeypatch, permanent=True, mapbox_plan=[NotFoundError("none")], opencage_plan=[_result("Ibadan", "opencage")])
    out = _run(svc.GeocodingService().forward_geocode("Ibadan"))
    assert out.provider == "opencage" and out.city == "Ibadan"
    env = Env(monkeypatch, permanent=True, mapbox_plan=[ProviderUnavailableError("down")], opencage_plan=[NotFoundError("none")])
    try:
        _run(svc.GeocodingService().forward_geocode("Nowhereville"))
    except ProviderUnavailableError:
        return                                            # an outage is reported in preference to "not found"
    raise AssertionError("expected the outage to win")


def test_forward_geocode_with_nothing_configured_says_so(monkeypatch):
    Env(monkeypatch, token="", opencage_key="")
    try:
        _run(svc.GeocodingService().forward_geocode("Paris"))
    except ProviderUnavailableError as exc:
        assert "not configured" in str(exc)
        return
    raise AssertionError("expected ProviderUnavailableError")


def test_permanent_mapbox_answers_are_cached_and_stored_but_temporary_ones_never(monkeypatch):
    env = Env(monkeypatch, permanent=True, mapbox_plan=[_result("Ibadan", "mapbox")])
    out = _run(svc.GeocodingService().forward_geocode("Ibadan"))
    assert out.provider == "mapbox" and env.redis and env.durable
    env2 = Env(monkeypatch, permanent=False)
    resp = svc.GeocodingService
    _run(resp._remember("geocode:x", "mapbox", svc.policy.Operation.FORWARD_GEOCODE, "forward", svc._result_to_response(_result("X", "mapbox"), source="live")))
    assert env2.redis == {} and env2.durable == {}


def test_open_data_answers_are_cached_and_stored_and_venues_use_a_separate_cache_key(monkeypatch):
    env = Env(monkeypatch)
    _run(svc.GeocodingService().forward_geocode("Olumo Rock", precise=True))
    _run(svc.GeocodingService().forward_geocode("Olumo Rock"))
    assert set(env.redis) == {"geocode:olumo rock|venue", "geocode:olumo rock"} and len(env.durable) == 2
    assert len(env.opencage.calls) == 2


def test_reverse_geocode_uses_the_kept_chain_and_caches_by_policy(monkeypatch):
    env = Env(monkeypatch, permanent=False)
    out = _run(svc.GeocodingService().reverse_geocode(7.38, 3.9))
    assert out.provider == "opencage" and env.opencage.calls[0][0] == "reverse" and env.redis
