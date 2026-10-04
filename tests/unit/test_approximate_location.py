"""IP-based approximate location: public-IP check, IPinfo location lookup (parsing, caching, private
IPs skipped), the resolution fallback chain, and the two endpoints that use it."""
from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from app.core.exceptions import LocationUnavailableError, ValidationAppError
from app.providers.geolocation.ipinfo_provider import IPinfoProvider, IPLocation, _parse_location
from app.utils.net import is_public_ip


def _run(coro):
    return asyncio.run(coro)


USER = SimpleNamespace(country="NG", timezone="Africa/Lagos")


# ---------------------------------------------------------------------------
# is_public_ip
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("ip", ["8.8.8.8", "102.89.34.1", "2606:4700:4700::1111"])
def test_public_addresses_are_public(ip):
    assert is_public_ip(ip)


@pytest.mark.parametrize("ip", ["127.0.0.1", "::1", "10.0.0.5", "192.168.1.20", "172.16.4.4", "169.254.1.1", "0.0.0.0", "", None, "not-an-ip"])
def test_local_private_and_invalid_addresses_are_not_public(ip):
    assert not is_public_ip(ip)


# ---------------------------------------------------------------------------
# IPinfo response parsing
# ---------------------------------------------------------------------------
def test_parse_location_reads_city_region_country_coordinates_and_timezone():
    loc = _parse_location({"city": "Osogbo", "region": "Osun", "country": "ng", "loc": "7.7827,4.5418", "timezone": "Africa/Lagos"})
    assert loc == IPLocation(country="NG", region="Osun", city="Osogbo", latitude=7.7827, longitude=4.5418, timezone="Africa/Lagos")


@pytest.mark.parametrize("loc", ["", "garbage", "1", "a,b", "95.0,10.0", "10.0,190.0"])
def test_parse_location_ignores_bad_coordinates(loc):
    parsed = _parse_location({"country": "NG", "loc": loc})
    assert parsed.latitude is None and parsed.longitude is None and parsed.country == "NG"


# ---------------------------------------------------------------------------
# IPinfoProvider.lookup_location: caching, private IPs, bogons, failures
# ---------------------------------------------------------------------------
@pytest.fixture()
def redis(monkeypatch):
    from app.services.cache_service import CacheService

    store = {}

    async def get_raw(key):
        return store.get(key)

    async def set_raw(key, value, ttl):
        store[key] = value

    monkeypatch.setattr(CacheService, "get_raw", staticmethod(get_raw))
    monkeypatch.setattr(CacheService, "set_raw", staticmethod(set_raw))
    return store


@pytest.fixture()
def token(monkeypatch):
    from app.core.config import settings

    monkeypatch.setattr(settings, "IPINFO_TOKEN", "tok")


def _patch_provider_call(monkeypatch, payload, calls):
    async def resilient(name, attempt, idempotent=True):
        calls.append(1)
        if isinstance(payload, Exception):
            raise payload
        return payload

    monkeypatch.setattr("app.providers.geolocation.ipinfo_provider.resilient_request", resilient)


def test_lookup_location_returns_and_caches_the_location(monkeypatch, redis, token):
    calls = []
    _patch_provider_call(monkeypatch, {"city": "Lagos", "country": "NG", "loc": "6.45,3.39"}, calls)
    provider = IPinfoProvider()
    first = _run(provider.lookup_location("102.89.34.1"))
    second = _run(provider.lookup_location("102.89.34.1"))
    assert first == second and first.city == "Lagos" and first.latitude == 6.45
    assert len(calls) == 1 and "ipinfo:location:102.89.34.1" in redis


def test_lookup_location_skips_private_and_loopback_addresses_without_calling_the_provider(monkeypatch, redis, token):
    calls = []
    _patch_provider_call(monkeypatch, {"country": "NG"}, calls)
    provider = IPinfoProvider()
    assert _run(provider.lookup_location("127.0.0.1")) is None
    assert _run(provider.lookup_location("192.168.0.9")) is None
    assert _run(provider.lookup_location(None)) is None
    assert calls == []


def test_lookup_location_returns_none_without_a_token(monkeypatch, redis):
    from app.core.config import settings

    monkeypatch.setattr(settings, "IPINFO_TOKEN", "")
    calls = []
    _patch_provider_call(monkeypatch, {"country": "NG"}, calls)
    assert _run(IPinfoProvider().lookup_location("8.8.8.8")) is None and calls == []


def test_lookup_location_treats_a_bogon_as_no_location_and_caches_that(monkeypatch, redis, token):
    calls = []
    _patch_provider_call(monkeypatch, {"ip": "10.1.1.1", "bogon": True}, calls)
    provider = IPinfoProvider()
    assert _run(provider.lookup_location("8.8.4.4")) is None
    assert _run(provider.lookup_location("8.8.4.4")) is None
    assert len(calls) == 1 and redis["ipinfo:location:8.8.4.4"] == ""


def test_lookup_location_degrades_to_none_when_the_provider_fails(monkeypatch, redis, token):
    calls = []
    _patch_provider_call(monkeypatch, RuntimeError("boom"), calls)
    assert _run(IPinfoProvider().lookup_location("8.8.8.8")) is None


def test_lookup_country_behaviour_is_unchanged(monkeypatch, redis, token):
    calls = []
    _patch_provider_call(monkeypatch, {"country": "gb"}, calls)
    assert _run(IPinfoProvider().lookup_country("1.2.3.4")) == "GB"
    assert redis["ipinfo:country:1.2.3.4"] == "GB"


# ---------------------------------------------------------------------------
# ApproximateLocationService: the fallback chain
# ---------------------------------------------------------------------------
def _service(monkeypatch, *, ip_location, geocode=None):
    from app.modules.location.service import ApproximateLocationService

    svc = ApproximateLocationService()
    geocode_calls = []

    async def lookup_location(ip):
        return ip_location

    async def forward_geocode(query):
        geocode_calls.append(query)
        if geocode is None:
            raise RuntimeError("geocoder down")
        return SimpleNamespace(latitude=geocode[0], longitude=geocode[1])

    monkeypatch.setattr(svc._ipinfo, "lookup_location", lookup_location)
    monkeypatch.setattr(svc._geocoding, "forward_geocode", forward_geocode)
    return svc, geocode_calls


def test_ip_with_coordinates_wins_and_needs_no_geocoding(monkeypatch):
    svc, geocode_calls = _service(
        monkeypatch, ip_location=IPLocation(country="NG", city="Osogbo", region="Osun", latitude=7.78, longitude=4.54, timezone="Africa/Lagos")
    )
    loc = _run(svc.resolve(user=USER, client_ip="102.89.34.1"))
    assert (loc.source, loc.city, loc.country_name, loc.latitude) == ("ip", "Osogbo", "Nigeria", 7.78)
    assert loc.approximate is True and geocode_calls == []


def test_ip_without_coordinates_is_geocoded_from_its_city(monkeypatch):
    svc, geocode_calls = _service(
        monkeypatch, ip_location=IPLocation(country="NG", city="Ibadan", region="Oyo"), geocode=(7.37, 3.94)
    )
    loc = _run(svc.resolve(user=USER, client_ip="102.89.34.1"))
    assert loc.source == "ip" and (loc.latitude, loc.longitude) == (7.37, 3.94)
    assert geocode_calls == ["Ibadan, Oyo, Nigeria"]


def test_falls_back_to_the_account_country_when_the_ip_cannot_be_located(monkeypatch):
    svc, geocode_calls = _service(monkeypatch, ip_location=None, geocode=(9.08, 8.68))
    loc = _run(svc.resolve(user=USER, client_ip="127.0.0.1"))
    assert loc.source == "account_country" and loc.country == "NG" and loc.city is None
    assert loc.timezone == "Africa/Lagos" and geocode_calls == ["Nigeria"]


def test_raises_location_unavailable_when_nothing_resolves(monkeypatch):
    svc, _ = _service(monkeypatch, ip_location=None, geocode=None)
    with pytest.raises(LocationUnavailableError) as exc:
        _run(svc.resolve(user=USER, client_ip="127.0.0.1"))
    assert exc.value.status_code == 422 and exc.value.error_code == "location_unavailable"


def test_raises_when_the_account_has_no_country_and_the_ip_is_unknown(monkeypatch):
    svc, _ = _service(monkeypatch, ip_location=None, geocode=(1.0, 1.0))
    with pytest.raises(LocationUnavailableError):
        _run(svc.resolve(user=SimpleNamespace(country=None, timezone=None), client_ip=None))


# ---------------------------------------------------------------------------
# Endpoints (called directly; the router decorators return the function unchanged)
# ---------------------------------------------------------------------------
def _weather(**overrides):
    from app.modules.weather.schemas import CurrentWeatherResponse

    base = dict(latitude=7.78, longitude=4.54, temperature_c=28.0, condition="Sunny", source="live", provider="weatherapi")
    return CurrentWeatherResponse(**{**base, **overrides})


def test_weather_current_with_coordinates_is_marked_explicit_and_skips_the_locator(monkeypatch):
    from app.api.routers import weather as router

    async def get_current(lat, lon):
        return _weather(latitude=lat, longitude=lon)

    async def boom(**kwargs):
        raise AssertionError("locator must not be used when coordinates are sent")

    monkeypatch.setattr(router._service, "get_current", get_current)
    monkeypatch.setattr(router._locator, "resolve", boom)
    result = _run(router.current_weather(latitude=1.0, longitude=2.0, current_user=USER, client_ip="8.8.8.8"))
    assert result.location_source == "explicit" and result.city is None


def test_weather_current_without_coordinates_uses_the_approximate_location(monkeypatch):
    from app.api.routers import weather as router
    from app.modules.location.schemas import ApproximateLocationResponse

    async def resolve(*, user, client_ip):
        assert client_ip == "102.89.34.1"
        return ApproximateLocationResponse(latitude=7.78, longitude=4.54, city="Osogbo", region="Osun", country="NG", country_name="Nigeria", source="ip")

    async def get_current(lat, lon):
        assert (lat, lon) == (7.78, 4.54)
        return _weather(latitude=lat, longitude=lon)

    monkeypatch.setattr(router._locator, "resolve", resolve)
    monkeypatch.setattr(router._service, "get_current", get_current)
    result = _run(router.current_weather(latitude=None, longitude=None, current_user=USER, client_ip="102.89.34.1"))
    assert (result.location_source, result.city, result.region, result.country) == ("ip", "Osogbo", "Osun", "NG")
    assert result.temperature_c == 28.0


def test_weather_current_with_only_one_coordinate_is_rejected():
    from app.api.routers import weather as router

    with pytest.raises(ValidationAppError):
        _run(router.current_weather(latitude=1.0, longitude=None, current_user=USER, client_ip="8.8.8.8"))


def test_location_approximate_endpoint_returns_the_resolved_location(monkeypatch):
    from app.api.routers import location as router
    from app.modules.location.schemas import ApproximateLocationResponse

    expected = ApproximateLocationResponse(latitude=9.08, longitude=8.68, country="NG", country_name="Nigeria", source="account_country")

    async def resolve(*, user, client_ip):
        return expected

    monkeypatch.setattr(router._locator, "resolve", resolve)
    assert _run(router.approximate_location(current_user=USER, client_ip="127.0.0.1")) == expected
    assert "ip" not in ApproximateLocationResponse.model_fields          # the raw IP is never returned
