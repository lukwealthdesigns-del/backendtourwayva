"""Public share links: token, link state, and the privacy whitelist of what a public viewer sees."""
from datetime import date, datetime, time, timedelta, timezone
from types import SimpleNamespace

import pytest

from app.modules.sharing import rules

NOW = datetime(2026, 1, 1, tzinfo=timezone.utc)


def test_tokens_are_unguessable_and_only_the_hash_is_comparable():
    a, b = rules.new_token(), rules.new_token()
    assert a != b and rules.looks_like_token(a) and rules.hash_token(a) != rules.hash_token(b)


@pytest.mark.parametrize("bad", [None, "", "short", "../../x" + "a" * 40, "has space" + "a" * 40, "x" * 300])
def test_malformed_tokens_never_reach_the_database(bad):
    assert not rules.looks_like_token(bad)


def test_expiry_is_clamped_and_optional():
    assert rules.clamp_expiry_days(None) is None
    assert rules.clamp_expiry_days(0) == 1 and rules.clamp_expiry_days(9999) == 365
    assert rules.expiry_from(NOW, None) is None
    assert rules.expiry_from(NOW, 7) == NOW + timedelta(days=7)


@pytest.mark.parametrize("revoked,expires,expected", [
    (None, None, "active"), (None, NOW + timedelta(days=1), "active"), (None, NOW, "expired"),
    (NOW, None, "revoked"), (NOW, NOW - timedelta(days=1), "revoked"),
])
def test_link_state(revoked, expires, expected):
    assert rules.link_state(revoked_at=revoked, expires_at=expires, now=NOW) == expected


def _trip():
    item = SimpleNamespace(
        item_type="activity", title="Louvre", description="Art", location_name="Rue de Rivoli", latitude=48.86, longitude=2.33,
        start_time=time(10, 0), end_time=time(12, 0), image_url="https://img/x.jpg", sort_order=0,
        # everything below is PRIVATE and must never appear
        estimated_cost=120.0, currency="EUR", provider="amadeus", external_id="EXT-1", booking_link="https://book/x", notes="my secret note", source="provider",
    )
    day = SimpleNamespace(day_number=1, date=date(2026, 5, 1), weather_summary="Sunny")
    trip = SimpleNamespace(title="Paris trip", destination="Paris, France", start_date=date(2026, 5, 1), end_date=date(2026, 5, 3), travelers=2, overview="Art and food",
                           budget_amount=5000, budget_currency="EUR", owner_id="owner-uuid", origin="Lagos", planning_outline={"secret": 1})
    return trip, [(day, [item])]


def test_public_view_contains_the_itinerary():
    out = rules.public_trip(*_trip())
    assert out["destination"] == "Paris, France" and out["travelers"] == 2
    item = out["days"][0]["items"][0]
    assert item["title"] == "Louvre" and item["start_time"] == "10:00:00" and item["latitude"] == 48.86


def test_public_view_never_leaks_private_fields():
    out = rules.public_trip(*_trip())
    text = repr(out)
    for secret in ("5000", "EUR", "owner-uuid", "Lagos", "my secret note", "EXT-1", "https://book/x", "amadeus", "120", "planning_outline"):
        assert secret not in text, f"{secret!r} leaked into the public view"
    assert set(out) == {"title", "destination", "start_date", "end_date", "travelers", "overview", "days"}
    assert set(out["days"][0]["items"][0]) == {"item_type", "title", "description", "location_name", "latitude", "longitude", "image_url", "start_time", "end_time"}
