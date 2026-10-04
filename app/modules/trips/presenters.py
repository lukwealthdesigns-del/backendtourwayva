"""
Builds TripResponse for the VIEWING member: the raw trip plus the backend-computed
display_status / bucket / archive flag / generation state (see display_status.py).
"""
from __future__ import annotations

from typing import Optional, Sequence

from app.core.config import settings
from app.db.models.trip import Trip, TripMember
from app.db.models.user import User
from app.modules.trips.display_status import (
    GENERATING,
    bucket_for,
    compute_display_status,
    today_for,
)
from app.modules.trips.generation_state import latest_generation
from app.modules.trips.schemas import TripResponse


async def trip_response(trip: Trip, *, membership: Optional[TripMember], user: User) -> TripResponse:
    generation = await latest_generation(trip.id)
    archived_at = membership.archived_at if membership is not None else None
    display = compute_display_status(
        status=trip.status,
        start_date=trip.start_date,
        end_date=trip.end_date,
        today=today_for(user.timezone),
        archived=archived_at is not None,
        generation_state=generation.state if generation is not None else None,
        upcoming_window_days=settings.TRIP_UPCOMING_WINDOW_DAYS,
    )
    response = TripResponse.model_validate(trip)
    return response.model_copy(update={
        "display_status": display,
        "bucket": bucket_for(display),
        "is_archived": archived_at is not None,
        "archived_at": archived_at,
        # A failed RE-generation on an already-planned trip keeps its itinerary and badge, but the
        # frontend still gets the failure here to show a retry banner.
        "generation": generation,
    })


async def trip_responses(
    pairs: Sequence[tuple[Trip, TripMember]], *, user: User
) -> list[TripResponse]:
    return [await trip_response(trip, membership=member, user=user) for trip, member in pairs]
