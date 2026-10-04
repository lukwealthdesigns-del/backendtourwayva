"""
Is an itinerary being generated for this trip right now — or did the last attempt fail?

Generation runs as a background job whose record lives in Redis (see planning/jobs.py). This reads
the trip's most recent job and reports a small, trip-shaped answer so the Trips list and the trip
page can show "generating" / "failed" — and resume polling — after a page reload.

  * queued / running            -> "generating"
  * ... but older than the worker lock window (a crashed worker never finishes the job)
                                -> "failed"  (code "generation_timed_out", retryable)
  * failed                      -> "failed"  (with the job's error code/message/retryable)
  * succeeded / no job / expired-> None

The job record expires after PLANNING_JOB_TTL_SECONDS (24 h): an old failure stops being reported
and the trip simply shows as a draft again, from which the user can generate once more.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from typing import Optional

from app.core.config import settings
from app.core.logging import get_logger
from app.modules.planning.jobs import JobStore
from app.modules.trips.schemas import TripGenerationInfo

logger = get_logger(__name__)

_QUEUE_GRACE_SECONDS = 60


def _parse(ts: Optional[str]) -> Optional[datetime]:
    if not ts:
        return None
    try:
        parsed = datetime.fromisoformat(ts)
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def interpret_job(job: Optional[dict], *, now: Optional[datetime] = None) -> Optional[TripGenerationInfo]:
    if not job:
        return None
    now = now or datetime.now(timezone.utc)
    status = job.get("status")

    if status in ("queued", "running"):
        created = _parse(job.get("created_at"))
        limit = timedelta(seconds=settings.PLANNING_LOCK_TTL_SECONDS + _QUEUE_GRACE_SECONDS)
        if created is not None and now - created > limit:
            return TripGenerationInfo(
                job_id=job["job_id"], state="failed", error_code="generation_timed_out",
                error_message="Generating this itinerary took too long. Please try again.", retryable=True,
            )
        return TripGenerationInfo(job_id=job["job_id"], state="generating")

    if status == "failed":
        error = job.get("error") or {}
        return TripGenerationInfo(
            job_id=job["job_id"], state="failed", error_code=error.get("code"),
            error_message=error.get("message"), retryable=error.get("retryable"),
        )
    return None


async def latest_generation(trip_id: uuid.UUID) -> Optional[TripGenerationInfo]:
    """Best-effort: an unreachable Redis means "no generation info", never a failed request."""
    try:
        return interpret_job(await JobStore.latest_for_trip(trip_id))
    except Exception as exc:  # noqa: BLE001
        logger.warning("trip_generation_state_failed", error=str(exc))
        return None
