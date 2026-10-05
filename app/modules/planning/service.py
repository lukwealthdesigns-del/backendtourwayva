"""
PlanningService — the authorized entry point for itinerary generation.

  start()    verify the caller may edit the trip, honour an Idempotency-Key,
             create a job and either queue it (background) or run it (inline)
  run_job()  execute the LangGraph workflow for a job (called by the Celery
             worker, or inline in development), one generation per trip at a
             time, and record the outcome on the job
  get_job()  status for polling; only the job's owner may read it

Everything about the trip (dates, destination, travellers, budget) is read
from the stored trip. The request can only add preferences.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any, Awaitable, Callable, Optional

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.constants import FeatureFlag
from app.core.exceptions import AppError, NotFoundError, ProviderUnavailableError
from app.core.logging import get_logger
from app.db.models.trip import Trip, TripPreferences
from app.db.models.user import User
from app.modules.entitlements.service import EntitlementService
from app.modules.planning.domain import TripSpec
from app.modules.planning.graph import PlanningState, build_generation_spec
from app.modules.planning.jobs import NODE_STAGE, STAGES, JobStore, progress_for, public_job
from app.modules.planning.schemas import GenerateItineraryRequest, TripPreferencesPayload
from app.modules.planning.workflow import WorkflowSpec, run_workflow
from app.modules.trips.service import TripService
from app.repositories.memory_repository import MemoryRepository
from app.repositories.preferences_repository import PreferencesRepository
from app.repositories.trip_preferences_repository import TripPreferencesRepository
from app.repositories.trip_repository import TripRepository
from app.repositories.user_repository import UserRepository

logger = get_logger(__name__)

Runner = Callable[[WorkflowSpec, dict[str, Any]], Awaitable[dict[str, Any]]]
_LIST_FIELDS = ("interests", "food_preferences", "must_see", "avoid")


async def _langgraph_runner(spec: WorkflowSpec, initial: dict[str, Any]) -> dict[str, Any]:
    return await run_workflow(spec, PlanningState, initial)


def merge_preferences(
    user_prefs: Optional[Any], trip_prefs: Optional[Any], request_prefs: Optional[dict[str, Any]]
) -> dict[str, Any]:
    """Precedence: this request > the trip's saved preferences > the user's
    profile defaults. Only what was VOLUNTARILY provided is used — nothing is
    assumed (Master Prompt §7)."""
    merged: dict[str, Any] = {}
    if user_prefs is not None:
        merged.update(
            travel_style=", ".join(user_prefs.travel_styles or []) or None,
            interests=list(user_prefs.interests or []),
            hotel_preference=user_prefs.accommodation_preference,
            transport_preference=user_prefs.transportation_preference,
            walking_preference=user_prefs.walking_preference,
            budget_level=user_prefs.budget_preference,
            food_preferences=list(user_prefs.dietary_preferences or []),
            accessibility_needs=list(user_prefs.accessibility_preferences or []),
        )
    if trip_prefs is not None:
        for name in ("travel_style", "pace", "walking_preference", "hotel_preference", "transport_preference"):
            value = getattr(trip_prefs, name, None)
            if value:
                merged[name] = value
        for name in _LIST_FIELDS:
            values = getattr(trip_prefs, name, None)
            if values:
                merged[name] = list(values)
    for name, value in (request_prefs or {}).items():
        if value not in (None, [], ""):
            merged[name] = value
    return merged


class GenerationCancelled(Exception):
    """Raised between workflow steps when the user asked to stop. Nothing is persisted before the final step, so a
    cancelled generation leaves the trip exactly as it was."""


class PlanningService:
    def __init__(self, db: AsyncSession, *, runner: Optional[Runner] = None, ports_factory: Optional[Callable] = None):
        self.db = db
        self.trips = TripService(db)
        self.trip_repo = TripRepository(db)
        self.trip_prefs_repo = TripPreferencesRepository(db)
        self._runner: Runner = runner or _langgraph_runner
        self._ports_factory = ports_factory

    # ------------------------------------------------------------------
    # Preferences
    # ------------------------------------------------------------------
    async def get_preferences(self, *, trip_id: uuid.UUID, user: User) -> Optional[TripPreferences]:
        await self.trips.get_trip_authorized(trip_id=trip_id, user_id=user.id)
        return await self.trip_prefs_repo.get(trip_id)

    async def save_preferences(
        self, *, trip_id: uuid.UUID, user: User, payload: TripPreferencesPayload
    ) -> TripPreferences:
        await self.trips.get_trip_authorized(trip_id=trip_id, user_id=user.id, require_editor=True)
        prefs = await self._upsert_preferences(trip_id, payload)
        await self.db.commit()
        return prefs

    async def _upsert_preferences(self, trip_id: uuid.UUID, payload: TripPreferencesPayload) -> TripPreferences:
        prefs = await self.trip_prefs_repo.get(trip_id)
        if prefs is None:
            prefs = await self.trip_prefs_repo.create(
                TripPreferences(trip_id=trip_id, interests=[], food_preferences=[], must_see=[], avoid=[])
            )
        for name, value in payload.model_dump(exclude_unset=True).items():
            if name in _LIST_FIELDS and value is None:
                value = []
            setattr(prefs, name, value)
        await self.trip_prefs_repo.save(prefs)
        return prefs

    # ------------------------------------------------------------------
    # Jobs
    # ------------------------------------------------------------------
    async def start(
        self,
        *,
        trip_id: uuid.UUID,
        user: User,
        request: GenerateItineraryRequest,
        idempotency_key: Optional[str],
    ) -> dict[str, Any]:
        await self.trips.get_trip_authorized(trip_id=trip_id, user_id=user.id, require_editor=True)

        job_id = uuid.uuid4()
        if idempotency_key:
            earlier = await JobStore.claim_idempotency(
                user_id=user.id, trip_id=trip_id, key=idempotency_key, job_id=job_id
            )
            if earlier:
                existing = await JobStore.get(earlier)
                if existing is not None and existing["user_id"] == str(user.id):
                    return public_job(existing)

        if request.preferences is not None:
            await self._upsert_preferences(trip_id, request.preferences)
            await self.db.commit()

        job = await JobStore.create(
            job_id=job_id, trip_id=trip_id, user_id=user.id,
            request=request.model_dump(mode="json", exclude={"preferences"}),
        )

        if settings.planning_execution == "background":
            try:
                from app.workers.celery_app import celery_app

                celery_app.send_task("planning.generate", args=[str(job_id)])
            except Exception as exc:  # noqa: BLE001
                logger.error("planning_enqueue_failed", error=str(exc))
                await self._finish(job, error={"code": "queue_unavailable", "message": "Could not queue the job.",
                                               "retryable": True})
                raise ProviderUnavailableError("The planning queue is unavailable. Please try again shortly.") from exc
            return public_job(job)

        return public_job(await self.run_job(job_id))

    async def get_job(self, *, trip_id: uuid.UUID, job_id: uuid.UUID, user: User) -> dict[str, Any]:
        job = await JobStore.get(job_id)
        # Same answer whether the job is unknown or someone else's.
        if job is None or job["user_id"] != str(user.id) or job["trip_id"] != str(trip_id):
            raise NotFoundError("Generation job not found.")
        await self.trips.get_trip_authorized(trip_id=trip_id, user_id=user.id)   # still a member of the trip
        return public_job(job)

    async def cancel_job(self, *, trip_id: uuid.UUID, job_id: uuid.UUID, user: User) -> dict[str, Any]:
        """Stop a queued or running generation. Takes effect at the next step boundary (a model call already in flight
        finishes first, but its result is discarded). Idempotent: cancelling a finished job just returns it."""
        job = await JobStore.get(job_id)
        if job is None or job["user_id"] != str(user.id) or job["trip_id"] != str(trip_id):
            raise NotFoundError("Generation job not found.")
        await self.trips.get_trip_authorized(trip_id=trip_id, user_id=user.id, require_editor=True)
        if job["status"] in ("queued", "running"):
            await JobStore.request_cancel(job["job_id"])
            if job["status"] == "queued":
                # No worker has picked it up yet: finish it here so the client sees "cancelled" immediately.
                job = await self._finish(job, cancelled=True)
        return public_job(job)

    async def run_job(self, job_id: uuid.UUID | str) -> dict[str, Any]:
        job = await JobStore.get(job_id)
        if job is None:
            raise NotFoundError("Generation job not found.")
        if job["status"] in ("succeeded", "failed", "cancelled"):
            return job                                    # already done: safe to call again
        if await JobStore.cancel_requested(job["job_id"]):
            return await self._finish(job, cancelled=True)   # cancelled while still queued: never starts
        trip_id = uuid.UUID(job["trip_id"])

        if not await JobStore.acquire_trip_lock(trip_id, job["job_id"]):
            return await self._finish(job, error={
                "code": "generation_in_progress",
                "message": "An itinerary is already being generated for this trip. Please wait for it to finish.",
                "retryable": True,
            })

        job["status"] = "running"
        await JobStore.save(job)
        try:
            state = await self._execute(job)
            if state.get("error"):
                return await self._finish(job, error=state["error"])
            return await self._finish(job, result=state["result"])
        except GenerationCancelled:
            await self.db.rollback()
            logger.info("planning_job_cancelled", job_id=str(job["job_id"]))
            return await self._finish(job, cancelled=True)
        except AppError as exc:
            await self.db.rollback()
            return await self._finish(job, error={"code": exc.error_code, "message": exc.message, "retryable": False})
        except Exception:  # noqa: BLE001
            await self.db.rollback()
            logger.exception("planning_job_crashed", job_id=str(job["job_id"]))
            return await self._finish(job, error={"code": "internal_error",
                                                  "message": "Something went wrong while planning. Please try again.",
                                                  "retryable": True})
        finally:
            await JobStore.release_trip_lock(trip_id, job["job_id"])

    async def _execute(self, job: dict[str, Any]) -> dict[str, Any]:
        trip_id, user_id = uuid.UUID(job["trip_id"]), uuid.UUID(job["user_id"])
        user = await UserRepository(self.db).get_by_id(user_id)
        trip = await self.trip_repo.get_trip(trip_id)
        if user is None or trip is None:
            raise NotFoundError("Trip not found.")
        # Re-check authorization at execution time: the job may have waited in a queue
        # and the user could have been removed from the trip since.
        await self.trips.get_trip_authorized(trip_id=trip_id, user_id=user_id, require_editor=True)
        # ...and their PLAN: a job that waited in the queue must not run for a user whose
        # plan lapsed (or for a feature an admin has since switched off).
        entitlements = EntitlementService(self.db)
        await entitlements.require(user_id, FeatureFlag.PLANNER)
        may_use_memory = await entitlements.has_feature(user_id, FeatureFlag.MEMORY)
        premium_ai = await entitlements.has_feature(user_id, FeatureFlag.PREMIUM_AI)

        request = job.get("request", {})
        preferences = merge_preferences(
            await PreferencesRepository(self.db).get(user_id),
            await self.trip_prefs_repo.get(trip_id),
            None,
        )
        memories = [
            m.content for m in await MemoryRepository(self.db).list_for_user(user_id, enabled_only=True)
            if m.expires_at is None or m.expires_at > datetime.now(timezone.utc)
        ][:10] if may_use_memory else []

        if self._ports_factory is not None:
            ports = self._ports_factory(self.db, user_id=user_id, trip_id=trip_id, premium_ai=premium_ai)
        else:
            from app.modules.planning.deps import build_ports

            ports = build_ports(self.db, user_id=user_id, trip_id=trip_id, premium_ai=premium_ai)

        initial: dict[str, Any] = {
            "spec": self._spec_from_trip(trip),
            "currency": (trip.budget_currency or user.currency or "USD").upper(),
            "language": user.language or "en",
            "preferences": preferences,
            "memories": memories,
            "options": {
                "city_code": request.get("city_code"),
                "include_hotels": request.get("include_hotels", True),
                "include_activities": request.get("include_activities", True),
            },
            "repair_attempts": 0, "repairs": [], "notes": [], "issues": [],
        }
        spec = build_generation_spec(ports)
        spec.nodes = {name: self._reporting(job, name, fn) for name, fn in spec.nodes.items()}
        return await self._runner(spec, initial)

    @staticmethod
    def _reporting(job: dict[str, Any], node: str, fn: Callable[[dict[str, Any]], Awaitable[dict[str, Any]]]):
        """Wrap a workflow node so the job records the stage it is entering. Progress is best effort: a Redis
        hiccup must never fail or slow a generation."""
        stage = NODE_STAGE.get(node)

        async def wrapped(state: dict[str, Any]) -> dict[str, Any]:
            try:
                stop = await JobStore.cancel_requested(job["job_id"])
            except Exception:  # noqa: BLE001 - if Redis hiccups we keep going rather than fail the generation
                stop = False
            if stop:
                raise GenerationCancelled()
            if stage and (job.get("progress") or {}).get("stage") != stage:
                try:
                    job["progress"] = progress_for(stage)
                    await JobStore.save(job)
                except Exception:  # noqa: BLE001
                    logger.warning("planning_progress_save_failed", job_id=str(job.get("job_id")), stage=stage)
            return await fn(state)

        return wrapped

    @staticmethod
    def _spec_from_trip(trip: Trip) -> TripSpec:
        return TripSpec(
            destination=trip.destination, origin=trip.origin, start_date=trip.start_date, end_date=trip.end_date,
            travelers=trip.travelers, budget_amount=trip.budget_amount, budget_currency=trip.budget_currency,
        )

    @staticmethod
    async def _finish(
        job: dict[str, Any], *, result: Optional[dict[str, Any]] = None, error: Optional[dict[str, Any]] = None,
        cancelled: bool = False,
    ) -> dict[str, Any]:
        job["status"] = "cancelled" if cancelled else ("succeeded" if error is None else "failed")
        if error is None and not cancelled:
            job["progress"] = {"stage": "done", "stage_index": len(STAGES), "stage_count": len(STAGES), "percent": 100}
        job["result"], job["error"] = result, error
        job["finished_at"] = datetime.now(timezone.utc).isoformat()
        return await JobStore.save(job)
