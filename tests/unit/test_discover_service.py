"""DiscoverService: error mapping, persistence port, profile loading (fakes, no DB/network)."""
from __future__ import annotations

import asyncio
import uuid
from types import SimpleNamespace as NS

import pytest

from app.core.exceptions import ProviderUnavailableError, ValidationAppError
from app.modules.discover import service as discover_module
from app.modules.discover.schemas import DiscoverSearchRequest
from app.modules.discover.service import DiscoverService
from app.services.cache_service import CacheService

REQUEST = DiscoverSearchRequest(budget_amount=700000, budget_currency="NGN", duration_days=7, travelers=2)


class _FakeDB:
    def __init__(self):
        self.commits = 0
        self.rollbacks = 0

    async def commit(self):
        self.commits += 1

    async def rollback(self):
        self.rollbacks += 1


class _FakeRepo:
    def __init__(self):
        self.searches, self.results = [], []

    async def create_search(self, search):
        search.id = uuid.uuid4()
        self.searches.append(search)
        return search

    async def create_result(self, result):
        self.results.append(result)
        return result


def _service(runner=None):
    service = DiscoverService.__new__(DiscoverService)
    service.db, service.repo, service.llm = _FakeDB(), _FakeRepo(), None
    service._runner = runner
    return service


def _item(name="Lisbon", total=630000.0):
    return {"destination": name, "country": "PT", "latitude": 38.7, "longitude": -9.1, "reasons": "Great food.",
            "cost_breakdown": {"accommodation": 1, "food": 1, "transport": 1, "activities": 1, "total": total,
                               "currency": "NGN", "source": "estimated"},
            "suggested_duration_days": 7, "weather_summary": None, "best_travel_period": "April to June",
            "image_url": None, "image_attribution": None, "relevant_activities": [], "over_budget": False,
            "trip_planning_cta": {"destination": name}, "score": 0.91, "season_fit": None}


def _run(coro):
    return asyncio.run(coro)


@pytest.mark.parametrize("code,expected", [
    ("ai_unavailable", ProviderUnavailableError), ("currency_unavailable", ProviderUnavailableError),
    ("no_destinations_found", ValidationAppError), ("ai_output_invalid", ValidationAppError),
])
def test_workflow_errors_map_to_the_right_api_errors(code, expected):
    async def runner(spec, initial):
        return {"error": {"code": code, "message": "nope", "retryable": True}}

    with pytest.raises(expected):
        _run(_service(runner).search(user_id=uuid.uuid4(), payload=REQUEST))


def test_a_successful_run_becomes_a_typed_response():
    async def runner(spec, initial):
        assert initial["request"]["budget_currency"] == "NGN"                 # the request is what the graph receives
        return {"outcome": {"search_id": uuid.uuid4(), "results": [_item()], "source": "live", "notes": ["n"]}}

    response = _run(_service(runner).search(user_id=uuid.uuid4(), payload=REQUEST))
    assert response.source == "live" and response.notes == ["n"]
    assert response.results[0].destination == "Lisbon" and response.results[0].score == 0.91


@pytest.fixture()
def cache(monkeypatch):
    store = {}

    async def set_json(key, value, ttl):
        store[key] = (value, ttl)

    monkeypatch.setattr(CacheService, "set_json", staticmethod(set_json))
    return store


def test_persist_stores_rows_and_caches_the_shared_ranked_list_only_for_live_searches(cache):
    service = _service()
    ports = service._build_ports(uuid.uuid4(), REQUEST)
    meta = {"source": "live", "cache_key": "discover:v2:abc", "notes": [], "cache_payload": [_item(), _item("Cairo")]}

    out = _run(ports.persist([_item()], meta))

    assert out["source"] == "live" and len(service.repo.searches) == 1 and len(service.repo.results) == 1
    row = service.repo.results[0]
    assert row.destination == "Lisbon" and row.estimated_total_cost == 630000.0 and row.score == 0.91
    assert service.db.commits == 1
    assert cache["discover:v2:abc"][0] == meta["cache_payload"] and cache["discover:v2:abc"][1] == 60 * 60 * 6

    cache.clear()
    _run(ports.persist([_item()], {**meta, "source": "cache"}))
    assert cache == {}                                                        # a cache hit never rewrites the cache


def test_profile_loading_collects_only_what_the_user_provided(monkeypatch):
    prefs = NS(interests=["food"], travel_styles=["relaxed", "culture"], accommodation_preference="hotel",
               transportation_preference=None)

    class _Prefs:
        def __init__(self, db):
            pass

        async def get(self, user_id):
            return prefs

    class _Memory:
        def __init__(self, db):
            pass

        async def list_for_user(self, user_id, enabled_only=False):
            assert enabled_only is True                                       # disabled memories are never used
            return [NS(content=f"fact {n}") for n in range(8)]

    class _History:
        def __init__(self, db):
            pass

        async def list_destinations_for_user(self, user_id):
            return [NS(destination="Paris")]

    class _Entitlements:
        memory_allowed = True

        def __init__(self, db):
            pass

        async def has_feature(self, user_id, flag):
            assert flag.value == "MEMORY"
            return _Entitlements.memory_allowed

    monkeypatch.setattr(discover_module, "EntitlementService", _Entitlements)
    monkeypatch.setattr(discover_module, "PreferencesRepository", _Prefs)
    monkeypatch.setattr(discover_module, "MemoryRepository", _Memory)
    monkeypatch.setattr(discover_module, "TravelHistoryRepository", _History)
    ports = _service()._build_ports(uuid.uuid4(), REQUEST)

    profile = _run(ports.load_profile())

    assert profile["interests"] == ["food"] and profile["travel_style"] == "relaxed, culture"
    assert profile["accommodation_preference"] == "hotel" and profile["visited"] == ["Paris"]
    assert len(profile["memories"]) == 5                                      # bounded

    _Entitlements.memory_allowed = False                                      # remembering is a plan feature
    assert _run(_service()._build_ports(uuid.uuid4(), REQUEST).load_profile())["memories"] == []
    _Entitlements.memory_allowed = True

    class _NoPrefs(_Prefs):
        async def get(self, user_id):
            return None

    monkeypatch.setattr(discover_module, "PreferencesRepository", _NoPrefs)
    bare = _run(_service()._build_ports(uuid.uuid4(), REQUEST).load_profile())
    assert "interests" not in bare and bare["visited"] == ["Paris"]
