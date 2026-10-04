"""Pure hotel-replacement logic (currency conversion, cost delta) and the orchestrating
service (authorization, live re-pricing, version snapshot) — with in-memory fakes."""
from __future__ import annotations

import asyncio
import uuid
from datetime import date
from types import SimpleNamespace as NS

import pytest

from app.core.constants import TripItemType
from app.core.exceptions import NotFoundError, ValidationAppError
from app.modules.itinerary import hotel_replacement_service as service_module
from app.modules.itinerary.hotel_replacement import HotelOffer, build_replacement, convert_offer_cost, cost_delta
from app.modules.itinerary.hotel_replacement_service import HotelReplacementService


def _run(coro):
    return asyncio.run(coro)


OFFER = HotelOffer(hotel_id="HX2", hotel_name="Hotel Lumiere", room_description="Deluxe room",
                   price_total=200.0, currency="EUR", provider="amadeus", latitude=48.86, longitude=2.35)


# ---------------------------------------------------------------------------
# Pure logic
# ---------------------------------------------------------------------------
def test_same_currency_needs_no_rate():
    assert convert_offer_cost(OFFER, trip_currency="EUR", rate=None) == 200.0


def test_a_different_currency_is_converted_with_the_given_rate():
    assert convert_offer_cost(OFFER, trip_currency="USD", rate=1.1) == 220.0


def test_an_unavailable_rate_means_the_cost_is_honestly_unknown_never_guessed():
    assert convert_offer_cost(OFFER, trip_currency="USD", rate=None) is None


def test_build_replacement_carries_provider_identity_and_location():
    plan = build_replacement(OFFER, trip_currency="EUR", rate=None, image_url="https://img/x")
    assert plan.title == "Hotel Lumiere" and plan.external_id == "HX2" and plan.provider == "amadeus"
    assert plan.estimated_cost == 200.0 and plan.currency == "EUR" and plan.image_url == "https://img/x"
    assert (plan.latitude, plan.longitude) == (48.86, 2.35)


def test_build_replacement_with_no_rate_falls_back_to_the_offers_own_currency():
    plan = build_replacement(OFFER, trip_currency="USD", rate=None)
    assert plan.currency == "EUR" and plan.estimated_cost == 0.0        # honestly unpriced, not a fabricated 0-cost claim


def test_cost_delta_reports_the_difference_and_is_none_when_either_side_is_unknown():
    assert cost_delta(before=600.0, after=450.0) == -150.0
    assert cost_delta(before=450.0, after=600.0) == 150.0
    assert cost_delta(before=None, after=600.0) is None and cost_delta(before=600.0, after=None) is None


# ---------------------------------------------------------------------------
# Service
# ---------------------------------------------------------------------------
class _FakeDB:
    def __init__(self):
        self.commits = 0

    async def commit(self):
        self.commits += 1


class _FakeItemRepo:
    def __init__(self, item, day):
        self.item, self.day, self.saved = item, day, None

    async def get_item(self, item_id):
        return self.item if item_id == self.item.id else None

    async def get_day(self, day_id):
        return self.day if day_id == self.day.id else None

    async def save_item(self, item):
        self.saved = item
        return item


class Env:
    def __init__(self, monkeypatch, *, item_type=TripItemType.HOTEL, offer=None, rate=1.1, trip_currency="USD",
                access_ok=True, offer_error=None):
        self.trip = NS(id=uuid.uuid4(), start_date=date(2026, 10, 1), end_date=date(2026, 10, 5), travelers=2,
                       budget_currency=trip_currency)
        self.day = NS(id=uuid.uuid4(), trip_id=self.trip.id)
        self.item = NS(id=uuid.uuid4(), trip_day_id=self.day.id, item_type=item_type, title="Old Hotel",
                       location_name="Old Hotel", description=None, estimated_cost=600.0, currency=trip_currency,
                       provider="amadeus", external_id="HX1", source="provider", booking_link="https://x",
                       image_url=None, latitude=48.85, longitude=2.34)
        self.repo = _FakeItemRepo(self.item, self.day)
        self.rate, self.offer, self.offer_error, self.access_ok = rate, offer or OFFER, offer_error, access_ok
        self.image_calls = []
        env = self

        class _Trips:
            def __init__(self, db):
                pass

            async def get_trip_authorized(self, *, trip_id, user_id, require_editor=False):
                if not env.access_ok:
                    from app.core.exceptions import ForbiddenError
                    raise ForbiddenError("no access")
                assert require_editor is True
                return env.trip

        class _Hotels:
            async def get_offer(self, *, hotel_id, check_in, check_out, adults):
                if env.offer_error:
                    raise env.offer_error
                assert adults == min(env.trip.travelers, 9)
                return env.offer

        class _Currency:
            async def get_rate(self, base, target):
                if env.rate is None:
                    raise RuntimeError("rate unavailable")
                return NS(rate=env.rate)

        class _Images:
            async def get_or_search(self, name):
                env.image_calls.append(name)
                return NS(url=f"https://img/{name}")

        class _Itinerary:
            def __init__(self, db):
                pass

            async def _snapshot_version(self, *, trip_id, actor_id, change_summary):
                env.snapshot_summary = change_summary
                return NS(version_number=7)

        monkeypatch.setattr(service_module, "TripService", _Trips)
        monkeypatch.setattr(service_module, "ItineraryService", _Itinerary)
        self.hotels, self.currency, self.images = _Hotels(), _Currency(), _Images()

    def service(self):
        service = HotelReplacementService.__new__(HotelReplacementService)
        service.db, service.repo = _FakeDB(), self.repo
        service.hotels, service.currency, service.images = self.hotels, self.currency, self.images
        return service


def test_a_successful_replacement_recalculates_cost_and_snapshots_a_version(monkeypatch):
    env = Env(monkeypatch, offer=NS(**{**OFFER.__dict__, "price_total": 200.0, "currency": "EUR"}), rate=1.1, trip_currency="USD")
    result = _run(env.service().replace(trip_id=env.trip.id, item_id=env.item.id, new_hotel_id="HX2", user_id=uuid.uuid4()))
    assert result.previous_cost == 600.0 and result.new_cost == 220.0 and result.currency == "USD"
    assert result.cost_delta == -380.0 and result.version_number == 7
    assert env.repo.saved.external_id == "HX2" and env.repo.saved.source == "provider" and env.repo.saved.booking_link is None
    assert env.repo.saved.image_url == "https://img/Hotel Lumiere"
    assert "Hotel Lumiere" in env.snapshot_summary


def test_same_currency_offer_needs_no_rate_lookup(monkeypatch):
    env = Env(monkeypatch, trip_currency="EUR", rate=None)   # rate would raise if ever called
    result = _run(env.service().replace(trip_id=env.trip.id, item_id=env.item.id, new_hotel_id="HX2", user_id=uuid.uuid4()))
    assert result.new_cost == 200.0 and result.currency == "EUR"


def test_an_unavailable_exchange_rate_leaves_the_cost_unknown_not_fabricated(monkeypatch):
    env = Env(monkeypatch, trip_currency="USD", rate=None)
    result = _run(env.service().replace(trip_id=env.trip.id, item_id=env.item.id, new_hotel_id="HX2", user_id=uuid.uuid4()))
    assert result.new_cost is None and env.repo.saved.currency == "EUR"     # kept in the offer's own currency


def test_only_a_hotel_item_can_be_replaced(monkeypatch):
    env = Env(monkeypatch, item_type=TripItemType.ACTIVITY)
    with pytest.raises(ValidationAppError):
        _run(env.service().replace(trip_id=env.trip.id, item_id=env.item.id, new_hotel_id="HX2", user_id=uuid.uuid4()))


def test_an_item_from_a_different_trip_is_not_found(monkeypatch):
    env = Env(monkeypatch)
    with pytest.raises(NotFoundError):
        _run(env.service().replace(trip_id=uuid.uuid4(), item_id=env.item.id, new_hotel_id="HX2", user_id=uuid.uuid4()))


def test_no_offer_available_for_the_dates_is_reported_clearly(monkeypatch):
    env = Env(monkeypatch)
    env.hotels = type("H", (), {"get_offer": staticmethod(lambda **k: _none())})()

    async def _none():
        return None

    with pytest.raises(ValidationAppError, match="no available offer"):
        _run(env.service().replace(trip_id=env.trip.id, item_id=env.item.id, new_hotel_id="HX2", user_id=uuid.uuid4()))


def test_requires_editor_access_to_the_trip(monkeypatch):
    env = Env(monkeypatch, access_ok=False)
    from app.core.exceptions import ForbiddenError

    with pytest.raises(ForbiddenError):
        _run(env.service().replace(trip_id=env.trip.id, item_id=env.item.id, new_hotel_id="HX2", user_id=uuid.uuid4()))


def test_a_failed_image_lookup_never_blocks_the_replacement(monkeypatch):
    env = Env(monkeypatch, trip_currency="EUR")

    async def broken(name):
        raise RuntimeError("unsplash down")

    env.images.get_or_search = broken
    result = _run(env.service().replace(trip_id=env.trip.id, item_id=env.item.id, new_hotel_id="HX2", user_id=uuid.uuid4()))
    assert result.new_cost == 200.0 and env.repo.saved.image_url is None
