"""SavedPlaceService: idempotent save, trip authorization, ownership on delete, and the
Companion's get_saved_places / save_place tools."""
from __future__ import annotations

import asyncio
import uuid
from types import SimpleNamespace as NS

import pytest

from app.core.constants import PlaceCategory
from app.core.exceptions import ForbiddenError, NotFoundError
from app.modules.saved_places import service as saved_places_module
from app.modules.saved_places.schemas import SavePlaceRequest
from app.modules.saved_places.service import SavedPlaceService


def _run(coro):
    return asyncio.run(coro)


class _FakeDB:
    def __init__(self):
        self.commits = 0

    async def commit(self):
        self.commits += 1


class _Repo:
    def __init__(self):
        self.places = {}

    async def create(self, place):
        place.id = uuid.uuid4()
        self.places[place.id] = place
        return place

    async def get(self, place_id):
        return self.places.get(place_id)

    async def list_for_user(self, user_id, *, trip_id=None):
        rows = [p for p in self.places.values() if p.user_id == user_id]
        if trip_id is not None:
            rows = [p for p in rows if p.trip_id == trip_id]
        return rows

    async def delete(self, place):
        self.places.pop(place.id, None)

    async def find_duplicate(self, user_id, *, name, latitude, longitude):
        for p in self.places.values():
            if p.user_id == user_id and p.name == name and abs(p.latitude - latitude) < 0.001 and abs(p.longitude - longitude) < 0.001:
                return p
        return None


def _service(monkeypatch, *, trip_owner_ok=True):
    service = SavedPlaceService.__new__(SavedPlaceService)
    service.db, service.repo = _FakeDB(), _Repo()

    class _Trips:
        def __init__(self, db):
            pass

        async def get_trip_authorized(self, *, trip_id, user_id, require_editor=False):
            if not trip_owner_ok:
                raise ForbiddenError("no access")
            return NS(id=trip_id)

    monkeypatch.setattr(saved_places_module, "TripService", _Trips)
    return service


def _payload(**over):
    base = dict(name="Louvre Museum", category=PlaceCategory.MUSEUM if hasattr(PlaceCategory, "MUSEUM") else PlaceCategory.OTHER,
                latitude=48.8606, longitude=2.3376, city="Paris", country="fr", notes="must see", source="manual")
    base.update(over)
    return SavePlaceRequest(**base)


def test_saving_a_new_place_creates_a_row_and_commits(monkeypatch):
    service = _service(monkeypatch)
    user_id = uuid.uuid4()
    result = _run(service.save(user_id=user_id, payload=_payload()))
    assert result.was_new is True and result.place.user_id == user_id
    assert result.place.country == "FR" and service.db.commits == 1


def test_saving_the_same_place_twice_is_idempotent(monkeypatch):
    service = _service(monkeypatch)
    user_id = uuid.uuid4()
    first = _run(service.save(user_id=user_id, payload=_payload()))
    second = _run(service.save(user_id=user_id, payload=_payload(notes="different notes, same place")))
    assert second.was_new is False and second.place.id == first.place.id
    assert len(_run(service.list_for_user(user_id))) == 1


def test_a_slightly_different_location_is_not_treated_as_a_duplicate(monkeypatch):
    service = _service(monkeypatch)
    user_id = uuid.uuid4()
    _run(service.save(user_id=user_id, payload=_payload()))
    _run(service.save(user_id=user_id, payload=_payload(latitude=48.9, longitude=2.3376)))
    assert len(_run(service.list_for_user(user_id))) == 2


def test_saving_with_a_trip_requires_access_to_that_trip(monkeypatch):
    service = _service(monkeypatch, trip_owner_ok=False)
    with pytest.raises(ForbiddenError):
        _run(service.save(user_id=uuid.uuid4(), payload=_payload(trip_id=uuid.uuid4())))


def test_listing_can_be_filtered_to_one_trip(monkeypatch):
    service = _service(monkeypatch)
    user_id, trip_id = uuid.uuid4(), uuid.uuid4()
    _run(service.save(user_id=user_id, payload=_payload(trip_id=trip_id)))
    _run(service.save(user_id=user_id, payload=_payload(name="Somewhere else", latitude=10.0, longitude=10.0)))
    assert len(_run(service.list_for_user(user_id))) == 2
    assert len(_run(service.list_for_user(user_id, trip_id=trip_id))) == 1


def test_only_the_owner_can_delete_a_saved_place(monkeypatch):
    service = _service(monkeypatch)
    owner = uuid.uuid4()
    place = _run(service.save(user_id=owner, payload=_payload())).place
    with pytest.raises(ForbiddenError):
        _run(service.delete(user_id=uuid.uuid4(), place_id=place.id))
    _run(service.delete(user_id=owner, place_id=place.id))
    assert _run(service.list_for_user(owner)) == []


def test_deleting_an_unknown_place_is_a_404(monkeypatch):
    service = _service(monkeypatch)
    with pytest.raises(NotFoundError):
        _run(service.delete(user_id=uuid.uuid4(), place_id=uuid.uuid4()))


# ---------------------------------------------------------------------------
# Companion tools
# ---------------------------------------------------------------------------
def test_the_save_place_tool_validates_and_delegates(monkeypatch):
    from app.modules.companion import tools as tools_module

    saved = []

    class FakeService:
        def __init__(self, db):
            pass

        async def save(self, *, user_id, payload):
            saved.append((user_id, payload))
            return NS(place=NS(id=uuid.uuid4(), name=payload.name), was_new=True)

    monkeypatch.setattr("app.modules.saved_places.service.SavedPlaceService", FakeService)
    ctx = NS(db=None, user=NS(id=uuid.uuid4()))
    result = _run(tools_module.execute_tool("save_place", {"name": "Eiffel Tower", "latitude": 48.85, "longitude": 2.29}, ctx))
    assert result["was_new"] is True and result["name"] == "Eiffel Tower" and saved[0][0] == ctx.user.id


def test_the_save_place_tool_rejects_bad_input_without_raising(monkeypatch):
    from app.modules.companion import tools as tools_module

    ctx = NS(db=None, user=NS(id=uuid.uuid4()))
    result = _run(tools_module.execute_tool("save_place", {"name": "X", "latitude": "not-a-number", "longitude": 2.0}, ctx))
    assert "error" in result


def test_the_get_saved_places_tool_lists_them(monkeypatch):
    from app.modules.companion import tools as tools_module

    class FakeService:
        def __init__(self, db):
            pass

        async def list_for_user(self, user_id, *, trip_id=None):
            return [NS(id=uuid.uuid4(), name="Louvre", category=PlaceCategory.OTHER, city="Paris", country="FR",
                       latitude=48.86, longitude=2.33, notes=None)]

    monkeypatch.setattr("app.modules.saved_places.service.SavedPlaceService", FakeService)
    ctx = NS(db=None, user=NS(id=uuid.uuid4()))
    result = _run(tools_module.execute_tool("get_saved_places", {}, ctx))
    assert result["count"] == 1 and result["places"][0]["name"] == "Louvre"
