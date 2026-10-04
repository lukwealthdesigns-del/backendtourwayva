"""
PlanningService (jobs, idempotency, locking, authorization), PlanningPersistence
and version chaining — with in-memory fakes for Redis and the database.
"""
from __future__ import annotations

import asyncio
import uuid
from datetime import date, time, timedelta, datetime, timezone
from types import SimpleNamespace as NS

import pytest

from app.core.config import settings
from app.core.constants import TripItemType, TripStatus
from app.core.exceptions import ConflictError, NotFoundError, ProviderUnavailableError
from app.modules.planning import persistence as persistence_module
from app.modules.planning.domain import PlannedDay, PlannedItem, PlannedTrip
from app.modules.planning.persistence import PlanningPersistence, item_kwargs
from app.modules.planning.schemas import GenerateItineraryRequest
from app.modules.planning.service import PlanningService, merge_preferences
from app.services.cache_service import CacheService


class _FakeDB:
    def __init__(self):
        self.commits = 0
        self.rollbacks = 0

    async def commit(self):
        self.commits += 1

    async def rollback(self):
        self.rollbacks += 1


@pytest.fixture()
def redis(monkeypatch):
    """In-memory stand-in for the Redis-backed CacheService."""
    store: dict[str, object] = {}

    async def get_json(key):
        return store.get(key)

    async def set_json(key, value, ttl):
        store[key] = value

    async def set_if_absent(key, value, ttl):
        if key in store:
            return False
        store[key] = value
        return True

    async def get_raw(key):
        return store.get(key)

    async def delete(key):
        store.pop(key, None)

    for name, fn in dict(get_json=get_json, set_json=set_json, set_if_absent=set_if_absent,
                         get_raw=get_raw, delete=delete).items():
        monkeypatch.setattr(CacheService, name, staticmethod(fn))
    return store


class _FakeTrips:
    def __init__(self, allowed=True):
        self.allowed = allowed
        self.calls = []

    async def get_trip_authorized(self, *, trip_id, user_id, require_editor=False):
        self.calls.append((trip_id, user_id, require_editor))
        if not self.allowed:
            raise NotFoundError("Trip not found.")
        return NS(id=trip_id)


def _service(execute=None, trips=None):
    service = PlanningService.__new__(PlanningService)
    service.db = _FakeDB()
    service.trips = trips or _FakeTrips()
    service.executed = []

    async def default_execute(job):
        service.executed.append(job["job_id"])
        return {"result": {"version_number": 1}}

    service._execute = execute or default_execute
    return service


def _user():
    return NS(id=uuid.uuid4(), currency="EUR", language="en")


def _run(coro):
    return asyncio.run(coro)


REQUEST = GenerateItineraryRequest(city_code="PAR")


# ---------------------------------------------------------------------------
# Jobs
# ---------------------------------------------------------------------------
def test_inline_generation_records_the_result_and_releases_the_lock(redis, monkeypatch):
    monkeypatch.setattr(settings, "PLANNING_EXECUTION", "inline")
    service, user, trip_id = _service(), _user(), uuid.uuid4()

    job = _run(service.start(trip_id=trip_id, user=user, request=REQUEST, idempotency_key=None))

    assert job["status"] == "succeeded" and job["result"] == {"version_number": 1} and job["error"] is None
    assert "request" not in job and "user_id" not in job          # the stored request/user are never echoed
    assert service.trips.calls[0] == (trip_id, user.id, True)      # owner/editor required
    assert not [k for k in redis if k.startswith("planning:lock:")]  # lock released


def test_a_workflow_error_becomes_a_failed_job_with_a_retry_hint(redis, monkeypatch):
    monkeypatch.setattr(settings, "PLANNING_EXECUTION", "inline")

    async def failing(job):
        return {"error": {"code": "ai_unavailable", "message": "later", "retryable": True}}

    job = _run(_service(failing).start(trip_id=uuid.uuid4(), user=_user(), request=REQUEST, idempotency_key=None))
    assert job["status"] == "failed" and job["error"]["code"] == "ai_unavailable" and job["error"]["retryable"]


def test_an_unexpected_crash_is_reported_generically_and_the_lock_is_released(redis, monkeypatch):
    monkeypatch.setattr(settings, "PLANNING_EXECUTION", "inline")

    async def crashing(job):
        raise RuntimeError("secret internal detail")

    service = _service(crashing)
    job = _run(service.start(trip_id=uuid.uuid4(), user=_user(), request=REQUEST, idempotency_key=None))
    assert job["status"] == "failed" and job["error"]["code"] == "internal_error"
    assert "secret" not in str(job)                                # no internals leak to the client
    assert service.db.rollbacks == 1 and not [k for k in redis if k.startswith("planning:lock:")]


def test_only_one_generation_per_trip_at_a_time(redis, monkeypatch):
    monkeypatch.setattr(settings, "PLANNING_EXECUTION", "inline")
    trip_id = uuid.uuid4()
    redis[f"planning:lock:{trip_id}"] = "another-job"               # a run is already in progress
    service = _service()
    job = _run(service.start(trip_id=trip_id, user=_user(), request=REQUEST, idempotency_key=None))
    assert job["status"] == "failed" and job["error"]["code"] == "generation_in_progress"
    assert service.executed == [] and redis[f"planning:lock:{trip_id}"] == "another-job"   # not stolen


def test_idempotency_key_returns_the_first_job_instead_of_running_twice(redis, monkeypatch):
    monkeypatch.setattr(settings, "PLANNING_EXECUTION", "inline")
    service, user, trip_id = _service(), _user(), uuid.uuid4()

    first = _run(service.start(trip_id=trip_id, user=user, request=REQUEST, idempotency_key="retry-key-123"))
    second = _run(service.start(trip_id=trip_id, user=user, request=REQUEST, idempotency_key="retry-key-123"))

    assert second["job_id"] == first["job_id"] and len(service.executed) == 1
    third = _run(service.start(trip_id=trip_id, user=user, request=REQUEST, idempotency_key="different-key-1"))
    assert third["job_id"] != first["job_id"]
    other_user = _run(service.start(trip_id=trip_id, user=_user(), request=REQUEST, idempotency_key="retry-key-123"))
    assert other_user["job_id"] not in (first["job_id"], third["job_id"])       # keys are per user


def test_a_finished_job_is_not_run_again(redis, monkeypatch):
    monkeypatch.setattr(settings, "PLANNING_EXECUTION", "inline")
    service = _service()
    job = _run(service.start(trip_id=uuid.uuid4(), user=_user(), request=REQUEST, idempotency_key=None))
    _run(service.run_job(job["job_id"]))
    assert len(service.executed) == 1


def test_background_mode_queues_the_job_and_does_not_run_it_in_the_request(redis, monkeypatch):
    monkeypatch.setattr(settings, "PLANNING_EXECUTION", "background")
    sent = []
    monkeypatch.setattr("app.workers.celery_app.celery_app.send_task", lambda name, args=None, **kw: sent.append((name, args)))
    service = _service()

    job = _run(service.start(trip_id=uuid.uuid4(), user=_user(), request=REQUEST, idempotency_key=None))

    assert job["status"] == "queued" and sent == [("planning.generate", [job["job_id"]])]
    assert service.executed == []


def test_queue_outage_fails_the_job_and_tells_the_caller(redis, monkeypatch):
    monkeypatch.setattr(settings, "PLANNING_EXECUTION", "background")

    def boom(*a, **k):
        raise ConnectionError("broker down")

    monkeypatch.setattr("app.workers.celery_app.celery_app.send_task", boom)
    with pytest.raises(ProviderUnavailableError):
        _run(_service().start(trip_id=uuid.uuid4(), user=_user(), request=REQUEST, idempotency_key=None))


def test_job_status_is_private_to_its_owner_and_trip(redis, monkeypatch):
    monkeypatch.setattr(settings, "PLANNING_EXECUTION", "inline")
    service, user, trip_id = _service(), _user(), uuid.uuid4()
    job = _run(service.start(trip_id=trip_id, user=user, request=REQUEST, idempotency_key=None))
    job_id = uuid.UUID(job["job_id"])

    assert _run(service.get_job(trip_id=trip_id, job_id=job_id, user=user))["status"] == "succeeded"
    with pytest.raises(NotFoundError):
        _run(service.get_job(trip_id=trip_id, job_id=job_id, user=_user()))         # someone else
    with pytest.raises(NotFoundError):
        _run(service.get_job(trip_id=uuid.uuid4(), job_id=job_id, user=user))        # wrong trip
    with pytest.raises(NotFoundError):
        _run(service.get_job(trip_id=trip_id, job_id=uuid.uuid4(), user=user))       # unknown job


def test_unauthorized_callers_cannot_start_generation(redis, monkeypatch):
    monkeypatch.setattr(settings, "PLANNING_EXECUTION", "inline")
    service = _service(trips=_FakeTrips(allowed=False))
    with pytest.raises(NotFoundError):
        _run(service.start(trip_id=uuid.uuid4(), user=_user(), request=REQUEST, idempotency_key=None))
    assert service.executed == []


# ---------------------------------------------------------------------------
# Preference merging
# ---------------------------------------------------------------------------
def test_preferences_precedence_request_over_trip_over_profile():
    profile = NS(travel_styles=["adventure", "food"], interests=["hiking"], accommodation_preference="hostel",
                 transportation_preference="train", walking_preference="high", budget_preference="budget",
                 dietary_preferences=["vegetarian"], accessibility_preferences=[])
    trip = NS(travel_style="relaxed getaway", pace="relaxed", walking_preference="low", hotel_preference=None,
              transport_preference=None, interests=[], food_preferences=[], must_see=["Louvre"], avoid=[])

    merged = merge_preferences(profile, trip, {"pace": "packed", "avoid": ["clubs"], "must_see": None})

    assert merged["pace"] == "packed"                       # request wins
    assert merged["walking_preference"] == "low"            # trip overrides profile
    assert merged["hotel_preference"] == "hostel"           # profile fills the gap
    assert merged["interests"] == ["hiking"] and merged["must_see"] == ["Louvre"] and merged["avoid"] == ["clubs"]
    assert merged["food_preferences"] == ["vegetarian"] and merged["travel_style"] == "relaxed getaway"


def test_nothing_is_assumed_when_no_preferences_exist():
    assert merge_preferences(None, None, None) == {}
    assert merge_preferences(None, None, {"pace": None, "avoid": []}) == {}


# ---------------------------------------------------------------------------
# Persistence
# ---------------------------------------------------------------------------
class _FakeTripRepo:
    def __init__(self, trip, days=(), items_by_day=None):
        self.trip = trip
        self.days = list(days)
        self.items = {day_id: list(v) for day_id, v in (items_by_day or {}).items()}
        self.added, self.deleted = [], []

    async def get_trip(self, trip_id):
        return self.trip

    async def list_days_for_trip(self, trip_id):
        return self.days

    async def add_day(self, day):
        day.id = uuid.uuid4()
        self.days.append(day)
        self.items[day.id] = []
        return day

    async def list_items_for_day(self, day_id):
        return list(self.items.get(day_id, []))

    async def delete_item(self, item):
        self.deleted.append(item)
        for items in self.items.values():
            if item in items:
                items.remove(item)

    async def add_item(self, item):
        self.added.append(item)
        return item

    async def save_trip(self, trip):
        return trip


def _trip(days=3):
    start = date(2026, 10, 1)
    return NS(id=uuid.uuid4(), start_date=start, end_date=start + timedelta(days=days - 1), overview=None,
              status=TripStatus.DRAFT)


def _plan(trip, currency="EUR"):
    days = []
    for n in range(3):
        items = [PlannedItem("activity", f"Thing {n}", start_time=time(10), end_time=time(11), estimated_cost=10.0,
                             currency=currency, source="estimated")]
        days.append(PlannedDay(n + 1, trip.start_date + timedelta(days=n), items, weather_summary=f"Sunny {n}"))
    days[0].items.insert(0, PlannedItem("hotel", "Hotel Lumiere", estimated_cost=600.0, currency=currency,
                                        provider="amadeus", source="provider", external_id="HX1",
                                        booking_link=None, image_url="https://img"))
    return PlannedTrip(overview="A lovely trip.", currency=currency, days=days)


def _persistence(trip, repo, monkeypatch):
    calls = []

    class _FakeItinerary:
        def __init__(self, db):
            pass

        async def _snapshot_version(self, *, trip_id, actor_id, change_summary):
            calls.append(change_summary)
            return NS(id=uuid.uuid4(), version_number=7)

    monkeypatch.setattr(persistence_module, "ItineraryService", _FakeItinerary)
    persist = PlanningPersistence.__new__(PlanningPersistence)
    persist.db, persist.trip_id, persist.actor_id, persist.repo = _FakeDB(), trip.id, uuid.uuid4(), repo
    persist.commit = True
    return persist, calls


def test_persist_creates_days_replaces_items_and_records_a_version(monkeypatch):
    trip = _trip()
    existing_day = NS(id=uuid.uuid4(), day_number=1, weather_summary=None)
    stale = NS(id=uuid.uuid4(), title="old manual item")
    repo = _FakeTripRepo(trip, days=[existing_day], items_by_day={existing_day.id: [stale]})
    persist, snapshots = _persistence(trip, repo, monkeypatch)

    out = _run(persist(_plan(trip), {"warnings": ["w"], "repairs": ["r"], "notes": [], "data_sources": {"hotels": "ok"}}))

    assert repo.deleted == [stale]                                       # old items replaced, not merged
    assert len(repo.days) == 3                                           # missing days created
    assert existing_day.weather_summary == "Sunny 0"
    assert len(repo.added) == 4 and out["items"] == 4 and out["days"] == 3
    hotel = next(i for i in repo.added if i.item_type == TripItemType.HOTEL)
    assert (hotel.provider, hotel.source, hotel.external_id) == ("amadeus", "provider", "HX1")
    assert next(i for i in repo.added if i.title == "Thing 1").provider == "ai"   # estimates are labelled as AI
    assert trip.overview == "A lovely trip." and trip.status == TripStatus.PLANNED
    assert snapshots == ["AI-generated itinerary."] and out["version_number"] == 7
    assert out["total_estimated_cost"] == 630.0 and out["warnings"] == ["w"] and persist.db.commits == 1


def test_persist_refuses_to_overwrite_a_trip_whose_dates_changed(monkeypatch):
    trip = _trip()
    plan = _plan(trip)
    trip.end_date += timedelta(days=2)                                   # edited while the AI was working
    repo = _FakeTripRepo(trip)
    persist, snapshots = _persistence(trip, repo, monkeypatch)
    with pytest.raises(ConflictError):
        _run(persist(plan, {}))
    assert repo.added == [] and repo.deleted == [] and snapshots == [] and persist.db.commits == 0


def test_item_kwargs_maps_every_planned_field():
    item = PlannedItem("restaurant", "Bistro", description="d", location_name="l", latitude=1.0, longitude=2.0,
                       start_time=time(12), end_time=time(13), estimated_cost=50.0, currency="EUR",
                       source="estimated", notes="n")
    day_id = uuid.uuid4()
    kwargs = item_kwargs(item, day_id, 3)
    assert kwargs["item_type"] is TripItemType.RESTAURANT and kwargs["trip_day_id"] == day_id
    assert kwargs["sort_order"] == 3 and kwargs["provider"] == "ai" and kwargs["source"] == "estimated"
    assert (kwargs["start_time"], kwargs["end_time"], kwargs["notes"]) == (time(12), time(13), "n")


# ---------------------------------------------------------------------------
# Version chaining (ItineraryService)
# ---------------------------------------------------------------------------
def test_versions_form_a_parent_chain_and_snapshots_are_complete():
    from app.modules.itinerary.service import ItineraryService

    previous = NS(id=uuid.uuid4(), version_number=4)
    trip = NS(id=uuid.uuid4(), current_version_number=4, overview="ov")
    day = NS(id=uuid.uuid4(), day_number=1, date=date(2026, 10, 1), weather_summary="Rain")
    item = NS(id=uuid.uuid4(), item_type=TripItemType.HOTEL, title="H", description="desc", location_name="H",
              latitude=1.0, longitude=2.0, start_time=None, end_time=None, estimated_cost=5.0, currency="EUR",
              provider="amadeus", source="provider", external_id="X", booking_link=None, image_url=None,
              notes="n", sort_order=0)
    saved = []

    class Repo:
        async def get_trip(self, trip_id):
            return trip

        async def list_days_for_trip(self, trip_id):
            return [day]

        async def list_items_for_day(self, day_id):
            return [item]

        async def list_versions(self, trip_id):
            return [previous]                                             # newest first

        async def add_version(self, version):
            saved.append(version)
            return version

        async def save_trip(self, t):
            return t

    service = ItineraryService.__new__(ItineraryService)
    service.db, service.repo = _FakeDB(), Repo()

    version = _run(service._snapshot_version(trip_id=trip.id, actor_id=uuid.uuid4(), change_summary="edit"))

    assert version.parent_version_id == previous.id and version.version_number == 5 and trip.current_version_number == 5
    snapshot_item = version.snapshot["days"][0]["items"][0]
    assert snapshot_item["description"] == "desc" and snapshot_item["provider"] == "amadeus"
    assert snapshot_item["external_id"] == "X" and version.snapshot["overview"] == "ov"
    assert version.snapshot["days"][0]["weather_summary"] == "Rain"


# ---------------------------------------------------------------------------
# Entitlement is re-checked when a queued job actually runs
# ---------------------------------------------------------------------------
from app.core.constants import FeatureFlag  # noqa: E402
from app.core.exceptions import FeatureUnavailableError, ForbiddenError  # noqa: E402
from app.modules.planning import service as planning_module  # noqa: E402


class _ExecuteEnv:
    def __init__(self, monkeypatch, *, denied=None, has=None):
        self.denied = denied or {}
        self.has = has if has is not None else {FeatureFlag.MEMORY: True, FeatureFlag.PREMIUM_AI: False}
        self.factory_kwargs = None
        self.initial = None
        env = self
        self.user = NS(id=uuid.uuid4(), currency="EUR", language="fr")
        self.trip = NS(id=uuid.uuid4(), destination="Paris", origin=None, start_date=date(2026, 10, 1),
                       end_date=date(2026, 10, 3), travelers=2, budget_amount=1000.0, budget_currency=None)

        class _Entitlements:
            def __init__(self, db):
                pass

            async def require(self, user_id, flag):
                if flag in env.denied:
                    raise env.denied[flag]

            async def has_feature(self, user_id, flag):
                return env.has.get(flag, False)

        class _Users:
            def __init__(self, db):
                pass

            async def get_by_id(self, user_id):
                return env.user

        class _Prefs:
            def __init__(self, db):
                pass

            async def get(self, user_id):
                return None

        class _Memory:
            def __init__(self, db):
                pass

            async def list_for_user(self, user_id, enabled_only=False):
                return [NS(content="likes museums", expires_at=None)]

        monkeypatch.setattr(planning_module, "EntitlementService", _Entitlements)
        monkeypatch.setattr(planning_module, "UserRepository", _Users)
        monkeypatch.setattr(planning_module, "PreferencesRepository", _Prefs)
        monkeypatch.setattr(planning_module, "MemoryRepository", _Memory)

        async def runner(spec, initial):
            env.initial = initial
            return {"result": {"version_number": 1}}

        service = PlanningService.__new__(PlanningService)
        service.db, service._runner = _FakeDB(), runner

        class _Trips:
            async def get_trip_authorized(self, **kwargs):
                return env.trip

        class _TripRepo:
            async def get_trip(self, trip_id):
                return env.trip

        class _TripPrefs:
            async def get(self, trip_id):
                return None

        service.trips, service.trip_repo, service.trip_prefs_repo = _Trips(), _TripRepo(), _TripPrefs()

        def factory(db, **kwargs):
            env.factory_kwargs = kwargs
            return NS(geocode=None, llm=None)

        service._ports_factory = factory
        self.service = service

    def run(self):
        job = {"job_id": str(uuid.uuid4()), "trip_id": str(self.trip.id), "user_id": str(self.user.id),
               "request": {"city_code": "PAR"}}
        return _run(self.service._execute(job))


def test_a_queued_job_is_refused_if_the_plan_no_longer_includes_planning(monkeypatch):
    env = _ExecuteEnv(monkeypatch, denied={FeatureFlag.PLANNER: ForbiddenError("no planner")})
    with pytest.raises(ForbiddenError):
        env.run()
    assert env.initial is None and env.factory_kwargs is None          # nothing was planned or paid for


def test_a_kill_switch_stops_a_queued_job_too(monkeypatch):
    env = _ExecuteEnv(monkeypatch, denied={FeatureFlag.PLANNER: FeatureUnavailableError("switched off")})
    with pytest.raises(FeatureUnavailableError):
        env.run()


def test_memories_and_the_strong_model_are_only_used_with_their_features(monkeypatch):
    env = _ExecuteEnv(monkeypatch, has={FeatureFlag.MEMORY: True, FeatureFlag.PREMIUM_AI: True})
    env.run()
    assert env.initial["memories"] == ["likes museums"] and env.factory_kwargs["premium_ai"] is True
    assert env.initial["language"] == "fr" and env.initial["currency"] == "EUR"

    plain = _ExecuteEnv(monkeypatch, has={})
    plain.run()
    assert plain.initial["memories"] == [] and plain.factory_kwargs["premium_ai"] is False


def test_a_lapsed_plan_becomes_a_failed_job_with_a_clear_code_and_frees_the_lock(redis, monkeypatch):
    monkeypatch.setattr(settings, "PLANNING_EXECUTION", "inline")
    env = _ExecuteEnv(monkeypatch, denied={FeatureFlag.PLANNER: ForbiddenError("no planner")})
    env.service.trips = _FakeTrips()
    job = _run(env.service.start(trip_id=env.trip.id, user=env.user, request=REQUEST, idempotency_key=None))
    assert job["status"] == "failed" and job["error"]["code"] == "forbidden" and job["error"]["retryable"] is False
    assert not [k for k in redis if k.startswith("planning:lock:")]
