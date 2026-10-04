"""
The user-facing trip status — computed on the BACKEND so the frontend never has to guess.

The database keeps a small lifecycle `status` (draft | planned | ongoing | completed | cancelled).
What a traveler SEES also depends on things that are not in that column: whether an itinerary is
being generated right now (or just failed), whether the trip starts soon, is under way or is over,
and whether they archived it. `compute_display_status` folds all of that into one value.

display_status (one badge per trip):
    archived    the viewing member archived it                        (wins over everything)
    generating  an itinerary is being generated right now              ("Planning" in the UI copy)
    failed      the FIRST generation failed and the trip is still an empty draft
    draft       created, no itinerary yet
    ready       itinerary generated; the trip starts more than TRIP_UPCOMING_WINDOW_DAYS away
    upcoming    itinerary generated; the trip starts within TRIP_UPCOMING_WINDOW_DAYS
    active      today falls between start_date and end_date
    completed   over (recorded as completed, or its end date has passed)
    cancelled   status = cancelled (no endpoint sets this yet; reserved so the UI can render it)

bucket (the Trips-page tabs; "All" is every bucket except archived):
    drafts      draft, generating, failed
    upcoming    ready, upcoming
    active      active
    completed   completed, cancelled
    archived    archived

Pure functions: no database, no clock (`today` is passed in) — trivially testable.
"""
from __future__ import annotations

from datetime import date, datetime, timezone
from typing import Optional
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from app.core.constants import TripStatus

ARCHIVED = "archived"
GENERATING = "generating"
FAILED = "failed"
DRAFT = "draft"
READY = "ready"
UPCOMING = "upcoming"
ACTIVE = "active"
COMPLETED = "completed"
CANCELLED = "cancelled"

DISPLAY_STATUSES = (ARCHIVED, GENERATING, FAILED, DRAFT, READY, UPCOMING, ACTIVE, COMPLETED, CANCELLED)

BUCKETS = ("all", "drafts", "upcoming", "active", "completed", "archived")

_BUCKET_OF = {
    DRAFT: "drafts", GENERATING: "drafts", FAILED: "drafts",
    READY: "upcoming", UPCOMING: "upcoming",
    ACTIVE: "active",
    COMPLETED: "completed", CANCELLED: "completed",
    ARCHIVED: "archived",
}


def today_for(timezone_name: Optional[str], *, now: Optional[datetime] = None) -> date:
    """The current calendar date in the viewer's timezone (UTC if unknown/invalid), so a trip
    that starts "today" is Active from midnight local time, not midnight UTC."""
    now = now or datetime.now(timezone.utc)
    if timezone_name:
        try:
            return now.astimezone(ZoneInfo(timezone_name)).date()
        except (ZoneInfoNotFoundError, ValueError, KeyError):
            pass
    return now.astimezone(timezone.utc).date()


def compute_display_status(
    *,
    status: TripStatus,
    start_date: date,
    end_date: date,
    today: date,
    archived: bool,
    generation_state: Optional[str],
    upcoming_window_days: int,
) -> str:
    """`generation_state` is None, "generating" or "failed" (see generation_state.py)."""
    if archived:
        return ARCHIVED
    if generation_state == GENERATING:
        return GENERATING
    if status == TripStatus.CANCELLED:
        return CANCELLED
    if status == TripStatus.COMPLETED:
        return COMPLETED
    if status == TripStatus.DRAFT:
        return FAILED if generation_state == FAILED else DRAFT

    # planned / ongoing: decided by the dates
    if end_date < today:
        return COMPLETED
    if start_date <= today <= end_date:
        return ACTIVE
    if status == TripStatus.ONGOING:
        return ACTIVE
    return UPCOMING if (start_date - today).days <= upcoming_window_days else READY


def bucket_for(display_status: str) -> str:
    return _BUCKET_OF[display_status]


def in_bucket(display_status: str, bucket: str) -> bool:
    """`all` = everything the member has not archived."""
    if bucket == "all":
        return display_status != ARCHIVED
    return bucket_for(display_status) == bucket
