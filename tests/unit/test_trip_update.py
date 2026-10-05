"""Trip metadata editing: the request shape, and the guards that keep an existing itinerary consistent."""
from __future__ import annotations

import asyncio
import uuid
from datetime import date
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from app.core.exceptions import ConflictError, ValidationAppError
from app.modules.trips import service as trips_service
from app.modules.trips.schemas import TripUpdateRequest
from app.modules.trips.service import TripService


def test_request_needs_at_least_one_field_and_rejects_unknown_ones():
    with pytest.raises(ValidationError):
        TripUpdateRequest()
    with pytest.raises(ValidationError):
        TripUpdateRequest(title="x", owner_id=str(uuid.uuid4()))
    assert TripUpdateRequest(title="Lisbon").model_fields_set == {"title"}


def test_end_before_start_is_rejected_when_both_are_sent():
    with pytest.raises(ValidationError):
        TripUpdateRequest(start_date=date(2030, 5, 10), end_date=date(2030, 5, 1))


class _Repo:
    def __init__(self, items=()):
        self.items, self.saved, self.days_added = list(items), 0, []

    async def list_items_for_trip(self, trip_id):
        return self.items

    async def list_days_for_trip(self, trip_id):
        return []

    async def add_day(self, day):
        self.days_added.append(day)

    async def save_trip(self, trip):
        self.saved += 1


class _Db:
    async def commit(self): ...
    async def flush(self): ...
    async def delete(self, obj): ...


def _service(items=(), locked=False, monkeypatch=None):
    svc = TripService.__new__(TripService)
    svc.db, svc.repo = _Db(), _Repo(items)
    trip = SimpleNamespace(id=uuid.uuid4(), title="Old", destination="Paris", start_date=date(2030, 6, 1), end_date=date(2030, 6, 5), travelers=2)

    async def authorized(**kw):
        return trip

    svc.get_trip_authorized = authorized

    class Jobs:
        @staticmethod
        async def trip_locked(_id):
            return locked

    import app.modules.planning.jobs as jobs
    monkeypatch.setattr(jobs, "JobStore", Jobs)
    return svc, trip


def _run(svc, **fields):
    return asyncio.run(svc.update_trip(trip_id=uuid.uuid4(), user_id=uuid.uuid4(), payload=TripUpdateRequest(**fields)))


def test_basics_can_always_change_even_with_an_itinerary(monkeypatch):
    svc, trip = _service(items=[object()], monkeypatch=monkeypatch)
    _run(svc, title="Lisbon trip", travelers=4)
    assert trip.title == "Lisbon trip" and trip.travelers == 4 and svc.repo.saved == 1 and svc.repo.days_added == []


def test_dates_and_destination_are_locked_once_an_itinerary_exists(monkeypatch):
    svc, trip = _service(items=[object()], monkeypatch=monkeypatch)
    with pytest.raises(ConflictError) as e:
        _run(svc, destination="Rome")
    assert e.value.details["reason"] == "itinerary_exists" and trip.destination == "Paris"
    with pytest.raises(ConflictError):
        _run(svc, start_date=date(2030, 6, 2))


def test_changing_dates_on_an_empty_draft_recreates_the_days(monkeypatch):
    svc, trip = _service(monkeypatch=monkeypatch)
    _run(svc, start_date=date(2030, 7, 1), end_date=date(2030, 7, 3))
    assert [d.day_number for d in svc.repo.days_added] == [1, 2, 3]
    assert svc.repo.days_added[0].date == date(2030, 7, 1) and trip.end_date == date(2030, 7, 3)


def test_structural_edits_wait_while_a_generation_is_running(monkeypatch):
    svc, _ = _service(locked=True, monkeypatch=monkeypatch)
    with pytest.raises(ConflictError):
        _run(svc, destination="Rome")


def test_resending_the_same_dates_is_not_a_structural_change(monkeypatch):
    svc, _ = _service(items=[object()], monkeypatch=monkeypatch)
    _run(svc, start_date=date(2030, 6, 1), end_date=date(2030, 6, 5), title="Same dates")


def test_range_over_90_days_is_rejected(monkeypatch):
    svc, _ = _service(monkeypatch=monkeypatch)
    with pytest.raises(ValidationAppError):
        _run(svc, end_date=date(2031, 1, 1))
