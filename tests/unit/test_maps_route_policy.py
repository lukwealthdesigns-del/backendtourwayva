"""MapsService applies the location data-use policy: which provider's path may be drawn on which map, and what may be cached."""
from __future__ import annotations

import asyncio

from app.core import location_policy as policy
from app.core.exceptions import NotFoundError
from app.modules.maps import service as maps_service
from app.providers.maps.interface import RouteResult, TravelMode

ARGS = dict(origin_lat=7.38, origin_lon=3.9, destination_lat=7.4, destination_lon=3.95, mode=TravelMode.DRIVING, include_polyline=True)


def _run(coro):
    return asyncio.run(coro)


class FakeRoute:
    def __init__(self, provider, polyline="abc"):
        self.provider, self.polyline, self.calls = provider, polyline, 0

    async def get_route(self, **kw):
        self.calls += 1
        return RouteResult(1000.0, 200.0, kw["mode"], self.provider, self.polyline if kw["include_polyline"] else None)


class Env:
    def __init__(self, monkeypatch, provider, *, route_cache=0):
        self.store, self.ttls = {}, {}
        env = self
        self.provider = FakeRoute(provider)

        class FakeCache:
            @staticmethod
            async def get_json(key):
                return env.store.get(key)

            @staticmethod
            async def set_json(key, value, ttl):
                env.store[key], env.ttls[key] = value, ttl

        class FakeResolver:
            def providers(self, capability):
                return [(provider, env.provider)]

        monkeypatch.setattr(maps_service, "CacheService", FakeCache)
        monkeypatch.setattr(maps_service, "get_resolver", lambda: FakeResolver())
        monkeypatch.setattr(maps_service.settings, "MAPBOX_ROUTE_CACHE_SECONDS", route_cache)


def test_a_mapbox_path_is_returned_for_a_mapbox_map_and_not_cached_by_default(monkeypatch):
    env = Env(monkeypatch, "mapbox")
    out = _run(maps_service.MapsService().get_route(**ARGS))
    assert out.polyline == "abc" and out.provider == "mapbox" and out.source == "live"
    assert env.store == {}
    _run(maps_service.MapsService().get_route(**ARGS))
    assert env.provider.calls == 2


def test_an_operator_can_enable_a_short_mapbox_route_cache(monkeypatch):
    env = Env(monkeypatch, "mapbox", route_cache=300)
    _run(maps_service.MapsService().get_route(**ARGS))
    assert list(env.ttls.values()) == [300]
    again = _run(maps_service.MapsService().get_route(**ARGS))
    assert again.source == "cache" and env.provider.calls == 1


def test_a_path_is_withheld_from_a_map_the_providers_terms_do_not_allow(monkeypatch):
    Env(monkeypatch, "mapbox")
    on_osm = _run(maps_service.MapsService().get_route(**ARGS, map_engine=policy.MapEngine.OSM))
    assert on_osm.polyline is None and on_osm.distance_meters == 1000.0       # the facts still come back
    Env(monkeypatch, "google_routes")
    assert _run(maps_service.MapsService().get_route(**ARGS)).polyline is None            # Google's path never lands on Mapbox
    assert _run(maps_service.MapsService().get_route(**ARGS, map_engine=policy.MapEngine.GOOGLE)).polyline == "abc"


def test_open_data_routes_are_cached_for_the_full_ttl_and_the_cache_key_includes_the_map(monkeypatch):
    env = Env(monkeypatch, "openrouteservice")
    _run(maps_service.MapsService().get_route(**ARGS))
    assert list(env.ttls.values()) == [maps_service.TTL_ROUTE_SECONDS]
    assert all(k.endswith(":path:mapbox") for k in env.store)
    _run(maps_service.MapsService().get_route(**ARGS, map_engine=policy.MapEngine.OSM))
    assert len(env.store) == 2


def test_a_route_that_cannot_exist_is_answered_without_calling_any_provider(monkeypatch):
    env = Env(monkeypatch, "mapbox")
    try:
        _run(maps_service.MapsService().get_route(origin_lat=7.4, origin_lon=3.9, destination_lat=25.2, destination_lon=55.3,
                                                  mode=TravelMode.WALKING))
    except NotFoundError:
        assert env.provider.calls == 0
        return
    raise AssertionError("expected NotFoundError")
