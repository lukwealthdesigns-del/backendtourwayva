"""Generation job state, idempotency claims and per-trip locks (Redis).

Long AI workflows must not hold an HTTP request open (§69), so a job is
created, run by a worker, and polled. State lives in Redis with a TTL —
results are durable in the database (the trip + its version); the job record
is only the handle used to report progress."""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any, Optional

from app.core.config import settings
from app.services.cache_service import CacheService

# The user-visible stages of a generation, in order, with the percent complete when each one STARTS. They map 1:1 onto
# real workflow nodes (see NODE_STAGE), so progress only moves when real work has finished. Drafting the plan is the
# long LLM call, which is why it owns the biggest slice.
STAGES: tuple[tuple[str, int], ...] = (
    ("locate", 0),      # find the destination on the map
    ("gather", 8),      # hotels, activities, weather
    ("currency", 38),   # convert prices to the trip currency
    ("draft", 46),      # the model drafts the day-by-day plan
    ("verify", 82),     # ground places, check times/budget, repair if needed
    ("save", 96),       # persist the itinerary
)
NODE_STAGE = {
    "geocode_destination": "locate", "gather_data": "gather", "normalize_currency": "currency",
    "draft_itinerary": "draft", "ground_items": "verify", "validate": "verify", "repair": "verify", "persist": "save",
}


def progress_for(stage: str) -> dict[str, Any]:
    """Progress payload for a stage: its index, the stage count and the percent complete as that stage begins."""
    names = [s for s, _ in STAGES]
    idx = names.index(stage)
    return {"stage": stage, "stage_index": idx, "stage_count": len(STAGES), "percent": STAGES[idx][1]}


_JOB_KEY = "planning:job:{job_id}"
_CANCEL_KEY = "planning:job:{job_id}:cancel"
_IDEM_KEY = "planning:idem:{user_id}:{trip_id}:{key}"
_LOCK_KEY = "planning:lock:{trip_id}"
_LATEST_KEY = "planning:latest:{trip_id}"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class JobStore:
    @staticmethod
    async def create(*, job_id: uuid.UUID, trip_id: uuid.UUID, user_id: uuid.UUID, request: dict[str, Any]) -> dict[str, Any]:
        job = {
            "job_id": str(job_id), "trip_id": str(trip_id), "user_id": str(user_id), "status": "queued",
            "created_at": _now(), "finished_at": None, "result": None, "error": None, "progress": None, "request": request,
        }
        await CacheService.set_json(_JOB_KEY.format(job_id=job_id), job, settings.PLANNING_JOB_TTL_SECONDS)
        # Pointer to the trip's most recent job, so a page reload can find a generation that is
        # still running (or one that just failed) without the client having kept the job id.
        await CacheService.set_raw(_LATEST_KEY.format(trip_id=trip_id), str(job_id), settings.PLANNING_JOB_TTL_SECONDS)
        return job

    @staticmethod
    async def latest_for_trip(trip_id: uuid.UUID | str) -> Optional[dict[str, Any]]:
        job_id = await CacheService.get_raw(_LATEST_KEY.format(trip_id=trip_id))
        return await JobStore.get(job_id) if job_id else None

    @staticmethod
    async def get(job_id: uuid.UUID | str) -> Optional[dict[str, Any]]:
        return await CacheService.get_json(_JOB_KEY.format(job_id=job_id))

    @staticmethod
    async def save(job: dict[str, Any]) -> dict[str, Any]:
        await CacheService.set_json(_JOB_KEY.format(job_id=job["job_id"]), job, settings.PLANNING_JOB_TTL_SECONDS)
        return job

    @staticmethod
    async def request_cancel(job_id: uuid.UUID | str) -> None:
        """Ask a queued/running job to stop at its next step boundary. A separate key (not a field on the job) so the
        worker, which re-saves the whole job record as it progresses, cannot overwrite the request."""
        await CacheService.set_raw(_CANCEL_KEY.format(job_id=job_id), "1", settings.PLANNING_JOB_TTL_SECONDS)

    @staticmethod
    async def cancel_requested(job_id: uuid.UUID | str) -> bool:
        return (await CacheService.get_raw(_CANCEL_KEY.format(job_id=job_id))) == "1"

    @staticmethod
    async def claim_idempotency(*, user_id: uuid.UUID, trip_id: uuid.UUID, key: str, job_id: uuid.UUID) -> Optional[str]:
        """Returns the job id of an EARLIER request with the same
        Idempotency-Key (so the caller returns it instead of starting a second
        run), or None if this request claimed the key."""
        redis_key = _IDEM_KEY.format(user_id=user_id, trip_id=trip_id, key=key)
        claimed = await CacheService.set_if_absent(redis_key, str(job_id), settings.PLANNING_JOB_TTL_SECONDS)
        if claimed is False:
            return await CacheService.get_raw(redis_key)
        return None

    @staticmethod
    async def acquire_trip_lock(trip_id: uuid.UUID, job_id: uuid.UUID | str) -> bool:
        """One generation per trip at a time. If Redis is unreachable we
        proceed (a stale write is preferable to blocking all planning)."""
        result = await CacheService.set_if_absent(
            _LOCK_KEY.format(trip_id=trip_id), str(job_id), settings.PLANNING_LOCK_TTL_SECONDS
        )
        return result is not False

    @staticmethod
    async def trip_locked(trip_id: uuid.UUID | str) -> bool:
        """True while a generation holds this trip's lock (so edits that would race with it must wait)."""
        return (await CacheService.get_raw(_LOCK_KEY.format(trip_id=trip_id))) is not None

    @staticmethod
    async def release_trip_lock(trip_id: uuid.UUID | str, job_id: uuid.UUID | str) -> None:
        key = _LOCK_KEY.format(trip_id=trip_id)
        if await CacheService.get_raw(key) == str(job_id):   # only release OUR lock
            await CacheService.delete(key)


def public_job(job: dict[str, Any]) -> dict[str, Any]:
    """The job as shown to the client: never echoes the stored request or user id."""
    out = {k: job[k] for k in ("job_id", "trip_id", "status", "created_at", "finished_at", "result", "error")}
    out["progress"] = job.get("progress")      # absent on jobs created before progress reporting existed
    return out


