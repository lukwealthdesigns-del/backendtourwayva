"""Generation progress: the stage plan is ordered/monotonic, every real node maps to a stage, and wrapping nodes
records the stage as work starts without ever failing the generation."""
from __future__ import annotations

import asyncio

from app.modules.planning import service as planning_service
from app.modules.planning.jobs import NODE_STAGE, STAGES, progress_for, public_job
from app.modules.planning.service import PlanningService
from app.modules.planning.workflow import WorkflowSpec, run_locally


def _run(coro):
    return asyncio.run(coro)


def test_stage_plan_is_ordered_and_percentages_increase():
    percents = [p for _, p in STAGES]
    assert percents == sorted(percents) and percents[0] == 0 and percents[-1] < 100
    assert len({s for s, _ in STAGES}) == len(STAGES)


def test_every_graph_node_that_does_work_maps_to_a_known_stage():
    names = {s for s, _ in STAGES}
    assert set(NODE_STAGE.values()) <= names
    for node in ("geocode_destination", "gather_data", "normalize_currency", "draft_itinerary", "ground_items", "validate", "persist"):
        assert node in NODE_STAGE


def test_progress_payload_shape():
    p = progress_for("draft")
    assert p == {"stage": "draft", "stage_index": 3, "stage_count": len(STAGES), "percent": 46}


def test_public_job_exposes_progress_but_not_the_request():
    job = {"job_id": "j", "trip_id": "t", "status": "running", "created_at": "x", "finished_at": None, "result": None,
           "error": None, "progress": progress_for("gather"), "request": {"secret": 1}, "user_id": "u"}
    out = public_job(job)
    assert out["progress"]["stage"] == "gather" and "request" not in out and "user_id" not in out
    del job["progress"]
    assert public_job(job)["progress"] is None


def test_wrapped_nodes_record_stages_in_order(monkeypatch):
    saved: list[str | None] = []

    class FakeStore:
        @staticmethod
        async def save(job):
            saved.append((job.get("progress") or {}).get("stage"))
            return job

    monkeypatch.setattr(planning_service, "JobStore", FakeStore)
    job = {"job_id": "j", "progress": None}

    async def node(state):
        return {}

    names = ["geocode_destination", "gather_data", "normalize_currency", "draft_itinerary", "ground_items", "validate", "persist"]
    nodes = {n: PlanningService._reporting(job, n, node) for n in names}
    edges = [(a, b) for a, b in zip(names, names[1:])]
    spec = WorkflowSpec(entry=names[0], nodes=nodes, edges=edges, finish=[names[-1]])
    _run(run_locally(spec, {}))
    # ground_items + validate share the "verify" stage: one save, not two
    assert saved == ["locate", "gather", "currency", "draft", "verify", "save"]


def test_progress_save_failure_never_breaks_generation(monkeypatch):
    class BrokenStore:
        @staticmethod
        async def save(job):
            raise RuntimeError("redis down")

    monkeypatch.setattr(planning_service, "JobStore", BrokenStore)
    ran = []

    async def node(state):
        ran.append(1)
        return {"x": 1}

    wrapped = PlanningService._reporting({"job_id": "j"}, "draft_itinerary", node)
    assert _run(wrapped({})) == {"x": 1} and ran == [1]
