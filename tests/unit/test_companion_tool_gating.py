"""execute_tool re-checks the user's plan for every feature-bound tool (independent of the agent)."""
import asyncio
import uuid
from types import SimpleNamespace as NS

import pytest

from app.core.constants import FeatureFlag
from app.core.exceptions import FeatureUnavailableError, ForbiddenError
from app.modules.companion import tools as tools_module
from app.modules.companion.intents import TOOL_FEATURES


@pytest.fixture()
def entitlements(monkeypatch):
    state = NS(denied={}, checked=[])

    class _Entitlements:
        def __init__(self, db):
            pass

        async def require(self, user_id, flag):
            state.checked.append(flag)
            if flag in state.denied:
                raise state.denied[flag]

    monkeypatch.setattr("app.modules.entitlements.service.EntitlementService", _Entitlements)
    return state


def _run_tool(name, monkeypatch):
    calls = []

    async def handler(args, ctx):
        calls.append(name)
        return {"ok": True}

    monkeypatch.setitem(tools_module._HANDLERS, name, handler)
    ctx = NS(db=None, user=NS(id=uuid.uuid4()))
    result = asyncio.run(tools_module.execute_tool(name, {}, ctx))
    return result, calls


def test_a_feature_bound_tool_is_refused_without_its_feature_and_the_handler_never_runs(entitlements, monkeypatch):
    entitlements.denied[FeatureFlag.HOTELS] = ForbiddenError("Your current plan does not include the 'HOTELS' feature.")
    result, calls = _run_tool("search_hotels", monkeypatch)
    assert "does not include" in result["error"] and calls == []


def test_a_kill_switch_reaches_the_model_as_a_plain_error(entitlements, monkeypatch):
    entitlements.denied[FeatureFlag.WEATHER] = FeatureUnavailableError("This feature is temporarily unavailable.")
    result, calls = _run_tool("get_weather", monkeypatch)
    assert "temporarily unavailable" in result["error"] and calls == []


def test_an_entitled_user_reaches_the_handler(entitlements, monkeypatch):
    result, calls = _run_tool("search_flights", monkeypatch)
    assert result == {"ok": True} and calls == ["search_flights"] and entitlements.checked == [FeatureFlag.FLIGHTS]


def test_tools_without_a_feature_skip_the_check(entitlements, monkeypatch):
    assert "geocode" not in TOOL_FEATURES
    result, calls = _run_tool("geocode", monkeypatch)
    assert result == {"ok": True} and entitlements.checked == []


def test_every_write_capable_tool_checks_planner_at_execution_time(entitlements, monkeypatch):
    entitlements.denied[FeatureFlag.PLANNER] = ForbiddenError("no planner")
    for name in ("propose_add_trip_item", "propose_update_trip_item", "propose_delete_trip_item",
                 "propose_itinerary_revision", "create_trip_version"):
        result, calls = _run_tool(name, monkeypatch)
        assert result == {"error": "no planner"} and calls == [], name
