"""Pure flight-offer validation/formatting, and the orchestrating service (authorization,
day/date matching, version snapshot) — with in-memory fakes."""
from __future__ import annotations

import asyncio
import uuid
from datetime import date, time
from types import SimpleNamespace as NS

import pytest

from app.core.constants import TripItemType
from app.core.exceptions import NotFoundError, ValidationAppError
from app.modules.itinerary import flight_booking_service as service_module
from app.modules.itinerary.flight_booking import FlightOffer, FlightOfferError, build_flight_item, validate_offer
from app.modules.itinerary.flight_booking_service import FlightBookingService


def _run(coro):
    return asyncio.run(coro)


def _offer(**over):
    base = dict(offer_id="F1", origin="LOS", destination="LHR", departure_time="2026-10-01T08:30:00",
                arrival_time="2026-10-01T15:45:00", duration_iso8601="PT7H15M", stops=0, airline_codes=["BA"],
                price_total=450.0, currency="usd", provider="amadeus", cabin="economy")
    base.update(over)
    return FlightOffer(**base)


# ---------------------------------------------------------------------------
# Pure logic
# ---------------------------------------------------------------------------
def test_a_well_formed_offer_validates_against_its_departure_day():
    validate_offer(_offer(), day_date=date(2026, 10, 1))


def test_arrival_must_be_after_departure():
    with pytest.raises(FlightOfferError, match="Arrival time must be after"):
        validate_offer(_offer(arrival_time="2026-10-01T05:00:00"), day_date=date(2026, 10, 1))


def test_price_must_be_positive():
    with pytest.raises(FlightOfferError, match="positive"):
        validate_offer(_offer(price_total=0), day_date=date(2026, 10, 1))


def test_iata_codes_must_be_three_letters():
    with pytest.raises(FlightOfferError, match="IATA"):
        validate_offer(_offer(origin="LG"), day_date=date(2026, 10, 1))


def test_the_offer_must_depart_on_the_selected_day():
    with pytest.raises(FlightOfferError, match="not the selected day"):
        validate_offer(_offer(), day_date=date(2026, 10, 2))


def test_malformed_timestamps_are_rejected_cleanly():
    with pytest.raises(FlightOfferError, match="Invalid departure_time"):
        validate_offer(_offer(departure_time="not-a-date"), day_date=date(2026, 10, 1))


def test_build_flight_item_formats_title_times_and_notes():
    plan = build_flight_item(_offer())
    assert plan.title == "Flight LOS \u2192 LHR (BA)"
    assert plan.start_time == "08:30" and plan.end_time == "15:45"
    assert plan.notes == "Direct \u00b7 PT7H15M \u00b7 economy" and plan.currency == "USD"
    assert plan.estimated_cost == 450.0 and plan.external_id == "F1" and plan.provider == "amadeus"


def test_a_red_eye_landing_the_next_calendar_day_has_no_end_time():
    plan = build_flight_item(_offer(departure_time="2026-10-01T23:30:00", arrival_time="2026-10-02T06:15:00"))
    assert plan.start_time == "23:30" and plan.end_time is None


def test_multiple_stops_and_carriers_are_summarized():
    plan = build_flight_item(_offer(stops=2, airline_codes=["BA", "AF"], cabin=None))
    assert plan.notes == "2 stops \u00b7 PT7H15M" and "BA/AF" in plan.title


# ---------------------------------------------------------------------------
# Service
# ---------------------------------------------------------------------------
class _FakeDB:
    def __init__(self):
        self.commits = 0

    async def commit(self):
        self.commits += 1


class _FakeFlightProvider:
    """reprice_offer stand-in. `mode` controls behavior:
      - "confirm": returns a repriced offer (verification succeeds)
      - "miss": returns None (cache miss / provider couldn't confirm)
      - "error": raises, exercising the try/except fallback in _reverify
    """

    def __init__(self, mode: str = "confirm", repriced_total: float = 450.0, repriced_currency: str = "USD"):
        self.mode = mode
        self.repriced_total = repriced_total
        self.repriced_currency = repriced_currency
        self.calls: list[str] = []

    async def reprice_offer(self, *, offer_id: str):
        self.calls.append(offer_id)
        if self.mode == "miss":
            return None
        if self.mode == "error":
            raise RuntimeError("boom")
        from app.providers.flights.interface import FlightOffer as ProviderFlightOffer

        return ProviderFlightOffer(
            offer_id=offer_id, origin="LOS", destination="LHR", departure_time="2026-10-01T08:30:00",
            arrival_time="2026-10-01T15:45:00", duration_iso8601="PT7H15M", stops=0, airline_codes=["BA"],
            cabin="economy", price_total=self.repriced_total, currency=self.repriced_currency, provider="amadeus",
        )


class Env:
    def __init__(self, monkeypatch, *, day_date=date(2026, 10, 1), access_ok=True, existing_items=None, flight_provider=None):
        self.trip = NS(id=uuid.uuid4())
        self.day = NS(id=uuid.uuid4(), trip_id=self.trip.id, date=day_date)
        self.saved_items = []
        self.access_ok = access_ok
        self.flight_provider = flight_provider or _FakeFlightProvider(mode="confirm")
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

        class _Repo:
            def __init__(self, db):
                pass

            async def get_day(self, day_id):
                return env.day if day_id == env.day.id else None

            async def list_items_for_day(self, day_id):
                return existing_items or []

            async def add_item(self, item):
                item.id = uuid.uuid4()
                env.saved_items.append(item)
                return item

        class _Itinerary:
            def __init__(self, db):
                pass

            async def _snapshot_version(self, *, trip_id, actor_id, change_summary):
                env.snapshot_summary = change_summary
                return NS(version_number=3)

        monkeypatch.setattr(service_module, "TripService", _Trips)
        monkeypatch.setattr(service_module, "TripRepository", _Repo)
        monkeypatch.setattr(service_module, "ItineraryService", _Itinerary)

    def service(self):
        service = FlightBookingService.__new__(FlightBookingService)
        service.db, service.repo = _FakeDB(), service_module.TripRepository(None)
        service._flight_provider = self.flight_provider
        return service


def test_adding_a_valid_flight_that_reprices_successfully_is_marked_verified(monkeypatch):
    env = Env(monkeypatch)
    result = _run(env.service().add_to_trip(trip_id=env.trip.id, day_id=env.day.id, offer=_offer(), user_id=uuid.uuid4()))
    assert result.estimated_cost == 450.0 and result.currency == "USD" and result.version_number == 3
    assert result.price_verified is True and result.verification_note is None
    item = env.saved_items[0]
    assert item.item_type == TripItemType.FLIGHT and item.source == "provider" and item.external_id == "F1"
    assert item.start_time == time(8, 30) and item.end_time == time(15, 45) and item.sort_order == 0
    assert "LOS" in env.snapshot_summary
    assert env.flight_provider.calls == ["F1"]


def test_a_repriced_offer_uses_the_live_confirmed_price_not_the_stale_snapshot(monkeypatch):
    provider = _FakeFlightProvider(mode="confirm", repriced_total=475.50, repriced_currency="GBP")
    env = Env(monkeypatch, flight_provider=provider)
    result = _run(env.service().add_to_trip(trip_id=env.trip.id, day_id=env.day.id, offer=_offer(price_total=450.0, currency="usd"), user_id=uuid.uuid4()))
    assert result.estimated_cost == 475.50 and result.currency == "GBP" and result.price_verified is True


def test_a_cache_miss_on_reprice_still_adds_the_flight_but_marks_it_unverified(monkeypatch):
    provider = _FakeFlightProvider(mode="miss")
    env = Env(monkeypatch, flight_provider=provider)
    result = _run(env.service().add_to_trip(trip_id=env.trip.id, day_id=env.day.id, offer=_offer(), user_id=uuid.uuid4()))
    assert result.price_verified is False
    assert result.verification_note is not None and "could not be re-confirmed" in result.verification_note
    item = env.saved_items[0]
    assert item.source == "client_snapshot"
    # The unverified snapshot price/currency are still used — never blocked, never silently upgraded.
    assert item.estimated_cost == 450.0 and item.currency == "USD"


def test_a_reprice_provider_error_falls_back_to_unverified_rather_than_raising(monkeypatch):
    provider = _FakeFlightProvider(mode="error")
    env = Env(monkeypatch, flight_provider=provider)
    result = _run(env.service().add_to_trip(trip_id=env.trip.id, day_id=env.day.id, offer=_offer(), user_id=uuid.uuid4()))
    assert result.price_verified is False
    assert env.saved_items[0].source == "client_snapshot"


def test_sort_order_appends_after_existing_items(monkeypatch):
    existing = [NS(sort_order=0), NS(sort_order=1)]
    env = Env(monkeypatch, existing_items=existing)
    _run(env.service().add_to_trip(trip_id=env.trip.id, day_id=env.day.id, offer=_offer(), user_id=uuid.uuid4()))
    assert env.saved_items[0].sort_order == 2


def test_a_day_from_a_different_trip_is_not_found(monkeypatch):
    env = Env(monkeypatch)
    with pytest.raises(NotFoundError):
        _run(env.service().add_to_trip(trip_id=uuid.uuid4(), day_id=env.day.id, offer=_offer(), user_id=uuid.uuid4()))


def test_a_structurally_invalid_offer_is_a_validation_error(monkeypatch):
    env = Env(monkeypatch)
    with pytest.raises(ValidationAppError, match="positive"):
        _run(env.service().add_to_trip(trip_id=env.trip.id, day_id=env.day.id, offer=_offer(price_total=0), user_id=uuid.uuid4()))
    assert env.saved_items == []
    # Structural validation happens BEFORE any reprice attempt — no point calling Amadeus for a malformed offer.
    assert env.flight_provider.calls == []


def test_a_mismatched_departure_date_is_a_validation_error(monkeypatch):
    env = Env(monkeypatch, day_date=date(2026, 10, 5))
    with pytest.raises(ValidationAppError, match="not the selected day"):
        _run(env.service().add_to_trip(trip_id=env.trip.id, day_id=env.day.id, offer=_offer(), user_id=uuid.uuid4()))


def test_requires_editor_access_to_the_trip(monkeypatch):
    env = Env(monkeypatch, access_ok=False)
    from app.core.exceptions import ForbiddenError

    with pytest.raises(ForbiddenError):
        _run(env.service().add_to_trip(trip_id=env.trip.id, day_id=env.day.id, offer=_offer(), user_id=uuid.uuid4()))
