"""Provider-agnostic travel layer: resolver ordering, data status, and the pure parsers (no network, no database)."""
from datetime import datetime, timedelta, timezone

import pytest

from app.providers.flight_status.aviationstack_parsing import parse_status
from app.providers.flights.travelpayouts_parsing import booking_link, iso_duration, parse_prices
from app.providers.google_maps import parsing as g
from app.providers.travel.resolver import Capability, ProviderResolver, parse_order
from app.providers.travel.status import DataStatus, downgrade_for_cache, is_expired, provenance


# ---- resolver ----
def _resolver(order, configured):
    r = ProviderResolver(lambda cap: order)
    for name in ("google_places", "amadeus", "booking"):
        r.register(Capability.HOTEL_DISCOVERY, name, is_configured=lambda n=name: n in configured, factory=lambda n=name: object())
    return r


def test_parse_order_lowercases_and_drops_blanks_and_repeats():
    assert parse_order(" Google_Places, amadeus ,,amadeus") == ["google_places", "amadeus"]
    assert parse_order("") == []


def test_resolver_uses_priority_order_and_skips_unconfigured_providers():
    r = _resolver("amadeus,google_places,booking", configured={"google_places", "booking"})
    assert r.names(Capability.HOTEL_DISCOVERY) == ["google_places", "booking"]
    assert r.available(Capability.HOTEL_DISCOVERY)


def test_resolver_with_nothing_configured_is_empty_not_an_error():
    r = _resolver("amadeus", configured=set())
    assert r.providers(Capability.HOTEL_DISCOVERY) == []
    assert r.first(Capability.HOTEL_DISCOVERY) is None
    assert not r.available(Capability.HOTEL_DISCOVERY)


def test_resolver_ignores_unregistered_names_and_reuses_instances():
    r = _resolver("nonsense,google_places", configured={"google_places"})
    first = r.first(Capability.HOTEL_DISCOVERY)
    assert first is r.first(Capability.HOTEL_DISCOVERY)


# ---- data status ----
def test_provenance_carries_the_standard_fields():
    now = datetime(2026, 1, 1, tzinfo=timezone.utc)
    p = provenance("google_places", "abc", DataStatus.VERIFIED_LIVE, ttl_seconds=60, source_url="u", now=now)
    assert p["data_status"] == "VERIFIED_LIVE" and p["provider_reference"] == "abc" and p["source_url"] == "u"
    assert p["expires_at"] == (now + timedelta(seconds=60)).isoformat()


def test_cache_downgrades_verified_to_cached_but_never_upgrades():
    assert downgrade_for_cache({"data_status": "VERIFIED_LIVE"})["data_status"] == "CACHED"
    assert downgrade_for_cache({"data_status": "ESTIMATED"})["data_status"] == "ESTIMATED"


def test_is_expired():
    past = (datetime.now(timezone.utc) - timedelta(seconds=5)).isoformat()
    assert is_expired({"expires_at": past}) and not is_expired({"expires_at": None})


# ---- Google Places / Routes ----
def test_places_parse_skips_incomplete_and_duplicate_places():
    raw = {"places": [
        {"id": "a", "displayName": {"text": "A"}, "location": {"latitude": 1, "longitude": 2}, "priceLevel": "PRICE_LEVEL_EXPENSIVE"},
        {"id": "a", "displayName": {"text": "A again"}, "location": {"latitude": 1, "longitude": 2}},
        {"id": "b", "displayName": {"text": "B"}},
        {"displayName": {"text": "C"}, "location": {"latitude": 1, "longitude": 2}},
    ]}
    out = g.parse_places(raw)
    assert [p["place_id"] for p in out] == ["a"]
    assert out[0]["price_level"] == "$$$"           # a qualitative level, never an amount


def test_route_duration_parsing():
    assert g.parse_duration_seconds("1234s") == 1234.0
    assert g.parse_duration_seconds("12.5s") == 12.5
    assert g.parse_duration_seconds(None) is None and g.parse_duration_seconds("12m") is None


def test_signed_photo_links_verify_expire_and_cannot_be_retargeted():
    name = "places/abc/photos/p1"
    exp, sig = g.sign_photo("secret", name, now=1000)
    assert g.verify_photo("secret", name, exp, sig, now=1001)
    assert not g.verify_photo("secret", name, exp, sig, now=exp + 1)               # expired
    assert not g.verify_photo("secret", "places/abc/photos/other", exp, sig, now=1001)   # different photo
    assert not g.verify_photo("other-secret", name, exp, sig, now=1001)
    assert not g.verify_photo("secret", "../etc/passwd", exp, sig, now=1001)


# ---- Travelpayouts ----
def test_travelpayouts_prices_are_per_all_adults_and_cached_links_carry_the_marker():
    data = {"currency": "usd", "data": [
        {"origin": "LOS", "destination": "LHR", "price": 600, "airline": "BA", "flight_number": "75", "departure_at": "2026-12-01T22:00:00+01:00", "transfers": 1, "duration_to": 395, "link": "/search/X?t=1"},
        {"origin": "LOS", "price": 5},
    ]}
    out = parse_prices(data, adults=2, marker="m1")
    assert len(out) == 1
    o = out[0]
    assert o["price_total"] == 1200.0 and o["currency"] == "USD" and o["stops"] == 1
    assert o["duration_iso8601"] == "PT6H35M" and o["offer_id"].startswith("tp:")
    assert o["booking_link"] == "https://www.aviasales.com/search/X?t=1&marker=m1"
    assert o["arrival_time"] == "2026-12-02T04:35:00+01:00"


def test_travelpayouts_helpers():
    assert iso_duration(45) == "PT45M" and iso_duration(455) == "PT7H35M"
    assert booking_link(None, "m") is None
    assert booking_link("/s", "") == "https://www.aviasales.com/s"


# ---- Aviationstack ----
def test_aviationstack_status_parses_and_never_guesses():
    data = {"data": [{"flight_date": "2026-10-10", "flight_status": "active", "flight": {"iata": "BA75"}, "airline": {"name": "British Airways"},
                      "departure": {"iata": "LOS", "delay": 15, "gate": "B4"}, "arrival": {"iata": "LHR"}}]}
    s = parse_status(data, "ba75")
    assert s["status"] == "active" and s["departure"]["delay_minutes"] == 15 and s["departure"]["gate"] == "B4"
    assert parse_status({"error": {"code": "x"}}, "BA75") is None
    assert parse_status({"data": []}, "BA75") is None
    assert parse_status({"data": [{"flight": {"iata": "XX1"}}]}, "BA75") is None
    assert parse_status({"data": [{"flight": {"iata": "BA75"}, "flight_status": "weird"}]}, "BA75")["status"] is None
