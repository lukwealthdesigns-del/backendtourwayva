"""Cancelling a generation: the flag is honoured at the next step boundary (and before anything is persisted), a
queued job is cancelled without running, and finishing as cancelled never reports a result or 100% progress."""
from __future__ import annotations

import asyncio

import pytest

from app.modules.planning import service as planning_service
from app.modules.planning.service import GenerationCancelled, PlanningService


def _run(coro):
    return asyncio.run(coro)


class _Store:
    flags: set = set()
    saved: list = []

    @staticmethod
    async def cancel_requested(job_id):
        return str(job_id) in _Store.flags

    @staticmethod
    async def save(job):
        _Store.saved.append(dict(job))
        return job


@pytest.fixture(autouse=True)
def _store(monkeypatch):
    _Store.flags, _Store.saved = set(), []
    monkeypatch.setattr(planning_service, "JobStore", _Store)


async def _node(state):
    return {"ran": True}


def test_node_runs_normally_when_no_cancel_was_requested():
    job = {"job_id": "j1", "progress": None}
    assert _run(PlanningService._reporting(job, "gather_data", _node)({})) == {"ran": True}


def test_node_raises_before_doing_any_work_once_cancel_is_requested():
    job = {"job_id": "j1", "progress": None}
    _Store.flags.add("j1")
    ran = []

    async def node(state):
        ran.append(1)
        return {}

    with pytest.raises(GenerationCancelled):
        _run(PlanningService._reporting(job, "persist", node)({}))
    assert ran == []                     # in particular: the persist step never runs


def test_cancel_flag_for_another_job_is_ignored():
    _Store.flags.add("other")
    assert _run(PlanningService._reporting({"job_id": "j1", "progress": None}, "validate", _node)({})) == {"ran": True}


def test_a_redis_failure_while_checking_never_fails_the_generation(monkeypatch):
    class Broken(_Store):
        @staticmethod
        async def cancel_requested(job_id):
            raise RuntimeError("redis down")

    monkeypatch.setattr(planning_service, "JobStore", Broken)
    assert _run(PlanningService._reporting({"job_id": "j1", "progress": None}, "validate", _node)({})) == {"ran": True}


def test_finishing_as_cancelled_has_no_result_error_or_full_progress():
    job = {"job_id": "j1", "status": "running", "progress": {"stage": "draft", "percent": 46}, "result": None, "error": None}
    out = _run(PlanningService._finish(job, cancelled=True))
    assert out["status"] == "cancelled" and out["result"] is None and out["error"] is None
    assert out["progress"]["percent"] == 46 and out["finished_at"]
