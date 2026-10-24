"""Mapbox geocoding + directions adapters. No network: httpx.AsyncClient is faked. Covers the request we send (endpoint, the
temporary/permanent flag, token handling) and how failures are classified ('no route' is an answer, an outage falls through)."""
from __future__ import annotations

import asyncio
import httpx

from app.core.config import settings
from app.core.exceptions import NotFoundError, ProviderUnavailableError, ValidationAppError
from app.core.resilience import reset_breakers
from app.providers.maps.interface import TravelMode
from app.providers.mapbox import client as mapbox_client
from app.providers.mapbox.geocoding_provider import MapboxGeocodingProvider, clean_query
from app.providers.mapbox.routing_provider import MapboxDirectionsProvider


def _run(coro):
    return asyncio.run(coro)


class _Resp:
    def __init__(self, status=200, payload=None, text=""):
        self.status_code, self._payload, self.text = status, payload if payload is not None else {}, text or str(payload)
        self.headers = {}
        self.request = httpx.Request("GET", "https://api.mapbox.com/x?access_token=SECRET")

    def json(self):
        return self._payload

    def raise_for_status(self):
        if self.status_code >= 400:
            raise httpx.HTTPStatusError("error", request=self.request, response=self)


def _install(monkeypatch, response):
    sent = {}

    class FakeClient:
        def __init__(self, *a, **k):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def get(self, url, params=None):
            sent["url"], sent["params"] = url, dict(params or {})
            return response

    monkeypatch.setattr(mapbox_client.httpx, "AsyncClient", FakeClient)
    monkeypatch.setattr(settings, "MAPBOX_ACCESS_TOKEN", "pk.test-token")
    reset_breakers()
    return sent


PLACE = {"features": [{"type": "Feature", "geometry": {"coordinates": [3.947, 7.3775]}, "properties": {
    "feature_type": "place", "name": "Ibadan", "place_formatted": "Oyo, Nigeria",
    "coordinates": {"longitude": 3.947, "latitude": 7.3775},
    "context": {"region": {"name": "Oyo"}, "country": {"name": "Nigeria", "country_code": "NG"}}}}]}


def test_clean_query_respects_mapbox_limits():
    assert clean_query("a;b  c") == "a b c"
    assert len(clean_query("x" * 400)) == 256
    assert len(clean_query(" ".join(["w"] * 40)).split()) == 20


def test_autocomplete_is_temporary_place_typed_and_never_marked_permanent(monkeypatch):
    sent = _install(monkeypatch, _Resp(payload=PLACE))
    out = _run(MapboxGeocodingProvider(permanent=True).search_places("ibad", 6))
    assert out[0].formatted_address == "Ibadan, Oyo, Nigeria" and out[0].country == "NG"
    assert sent["url"].endswith("/search/geocode/v6/forward")
    assert sent["params"]["permanent"] == "false" and sent["params"]["autocomplete"] == "true"
    assert "place" in sent["params"]["types"] and "address" not in sent["params"]["types"]
    assert sent["params"]["access_token"] == "pk.test-token"


def test_forward_geocode_follows_the_permanent_flag_and_turns_autocomplete_off(monkeypatch):
    sent = _install(monkeypatch, _Resp(payload=PLACE))
    assert _run(MapboxGeocodingProvider(permanent=True).forward_geocode("Ibadan")).city == "Ibadan"
    assert sent["params"]["permanent"] == "true" and sent["params"]["autocomplete"] == "false"
    _run(MapboxGeocodingProvider(permanent=False).forward_geocode("Ibadan"))
    assert sent["params"]["permanent"] == "false"


def test_precise_lookups_ask_for_addresses_only_and_report_not_found_for_a_city_answer(monkeypatch):
    sent = _install(monkeypatch, _Resp(payload=PLACE))
    try:
        _run(MapboxGeocodingProvider(precise=True).forward_geocode("Olumo Rock Hotel"))
    except NotFoundError:
        pass
    else:
        raise AssertionError("a city centre must not stand in for a venue")
    assert sent["params"]["types"] == "address"


def test_reverse_geocode_uses_the_reverse_endpoint_with_lon_lat(monkeypatch):
    sent = _install(monkeypatch, _Resp(payload=PLACE))
    assert _run(MapboxGeocodingProvider().reverse_geocode(7.3775, 3.947)).city == "Ibadan"
    assert sent["url"].endswith("/search/geocode/v6/reverse")
    assert sent["params"]["longitude"] == "3.947000" and sent["params"]["latitude"] == "7.377500"


def test_geocoding_failures_are_classified(monkeypatch):
    _install(monkeypatch, _Resp(status=422, payload={"message": "Query too long"}))
    try:
        _run(MapboxGeocodingProvider().forward_geocode("x"))
    except NotFoundError:
        pass
    else:
        raise AssertionError("422 means a query Mapbox rejects: nothing to find, not an outage")
    for status in (401, 403, 500):
        _install(monkeypatch, _Resp(status=status, payload={"message": "no"}))
        reset_breakers()
        try:
            _run(MapboxGeocodingProvider().forward_geocode("x"))
        except ProviderUnavailableError:
            continue
        raise AssertionError(f"{status} should be an outage so the next provider is tried")


def test_not_configured_means_unavailable_without_calling_mapbox(monkeypatch):
    sent = _install(monkeypatch, _Resp(payload=PLACE))
    monkeypatch.setattr(settings, "MAPBOX_ACCESS_TOKEN", "")
    try:
        _run(MapboxGeocodingProvider().search_places("ibadan"))
    except ProviderUnavailableError:
        pass
    else:
        raise AssertionError("expected ProviderUnavailableError")
    assert sent == {}


ROUTE = {"code": "Ok", "routes": [{"distance": 5400.2, "duration": 780.5, "geometry": "_p~iF~ps|U_ulLnnqC"}]}


def test_directions_request_shape_and_result(monkeypatch):
    sent = _install(monkeypatch, _Resp(payload=ROUTE))
    r = _run(MapboxDirectionsProvider().get_route(origin_lat=7.38, origin_lon=3.9, destination_lat=7.4, destination_lon=3.95,
                                                  mode=TravelMode.DRIVING, include_polyline=True))
    assert (r.distance_meters, r.duration_seconds, r.provider, r.polyline) == (5400.2, 780.5, "mapbox", "_p~iF~ps|U_ulLnnqC")
    assert sent["url"].endswith("/directions/v5/mapbox/driving/3.900000,7.380000;3.950000,7.400000")   # longitude first
    assert sent["params"]["overview"] == "full" and sent["params"]["geometries"] == "polyline"


def test_directions_asks_for_no_geometry_unless_a_path_is_wanted(monkeypatch):
    sent = _install(monkeypatch, _Resp(payload=ROUTE))
    r = _run(MapboxDirectionsProvider().get_route(origin_lat=1, origin_lon=1, destination_lat=2, destination_lon=2, mode=TravelMode.WALKING))
    assert sent["params"]["overview"] == "false" and r.polyline is None and "/walking/" in sent["url"]


def test_transit_is_refused_so_the_next_provider_can_try(monkeypatch):
    _install(monkeypatch, _Resp(payload=ROUTE))
    try:
        _run(MapboxDirectionsProvider().get_route(origin_lat=1, origin_lon=1, destination_lat=2, destination_lon=2, mode=TravelMode.TRANSIT))
    except ValidationAppError:
        pass
    else:
        raise AssertionError("expected ValidationAppError")


def test_no_route_is_an_answer_and_other_failures_are_outages(monkeypatch):
    args = dict(origin_lat=1, origin_lon=1, destination_lat=2, destination_lon=2, mode=TravelMode.DRIVING)
    for resp in (_Resp(payload={"code": "NoRoute"}),
                 _Resp(status=422, payload={"code": "InvalidInput", "message": "Route exceeds maximum distance limitation"}),
                 _Resp(status=404, payload={"code": "NoSegment"})):
        _install(monkeypatch, resp)
        try:
            _run(MapboxDirectionsProvider().get_route(**args))
        except NotFoundError:
            continue
        raise AssertionError(f"{resp.status_code} {resp.text} should be 'no route'")
    for resp in (_Resp(status=401, payload={"message": "Not Authorized"}), _Resp(status=500, payload={})):
        _install(monkeypatch, resp)
        reset_breakers()
        try:
            _run(MapboxDirectionsProvider().get_route(**args))
        except ProviderUnavailableError:
            continue
        raise AssertionError(f"{resp.status_code} should be an outage")
