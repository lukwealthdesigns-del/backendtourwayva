"""
Itinerary generation endpoints (Master Prompt §13-14).

  POST /trips/{trip_id}/generate            start (202 queued / 200 finished inline)
  GET  /trips/{trip_id}/generate/{job_id}   poll a job
  POST /trips/{trip_id}/generate/{job_id}/cancel   stop a queued/running job
  GET  /trips/{trip_id}/preferences         planner preferences for the trip
  PUT  /trips/{trip_id}/preferences         update them (owner/editor)

Generation is gated on the PLANNER feature flag, requires owner/editor access to
the trip (re-checked when the job actually runs), is rate limited (it costs real
money), and honours an `Idempotency-Key` header so a retried request never
starts a second run.
"""
from __future__ import annotations

import uuid
from typing import Optional

from fastapi import APIRouter, Depends, Header, Response, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies import get_current_user, rate_limit, require_feature
from app.core.constants import FeatureFlag
from app.db.models.user import User
from app.db.session import get_db
from app.modules.planning.policy import PlanningPolicyService
from app.modules.planning.policy_schemas import EffectivePlanningLimits
from app.modules.planning.schemas import (
    GenerateItineraryRequest,
    GenerationJobResponse,
    TripPreferencesPayload,
    TripPreferencesResponse,
)
from app.modules.planning.service import PlanningService

router = APIRouter(prefix="/trips", tags=["Planning"])
limits_router = APIRouter(prefix="/planning", tags=["Planning"])


@limits_router.get("/limits", response_model=EffectivePlanningLimits)
async def my_planning_limits(current_user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    """What the signed-in user can plan right now: the longest trip, how many days are planned in detail in one go,
    the size of each detailed part for longer trips, and the monthly generation allowance (0 = unlimited). The trip
    wizard reads this to set the calendar and to explain how long trips work. `growth` is true while the admin's
    growth mode is on (the generous launch limits apply to everyone)."""
    service = PlanningPolicyService(db)
    limits = await service.limits_for(current_user.id)
    used = await service.generations_this_month(current_user.id)
    remaining = max(0, limits.monthly_limit - used) if limits.monthly_limit > 0 else -1     # -1 = unlimited
    return EffectivePlanningLimits(
        tier=limits.tier, growth=limits.growth, max_days=limits.max_days, full_detail_max_days=limits.full_detail_max_days,
        chunk_days=limits.chunk_days, monthly_limit=limits.monthly_limit, used_this_month=used, remaining_this_month=remaining,
    )


@router.post(
    "/{trip_id}/generate",
    response_model=GenerationJobResponse,
    dependencies=[
        Depends(require_feature(FeatureFlag.PLANNER)),
        Depends(rate_limit(bucket="planning:generate", max_requests=6, window_seconds=3600, per="user")),
    ],
)
async def generate_itinerary(
    trip_id: uuid.UUID,
    payload: GenerateItineraryRequest,
    response: Response,
    idempotency_key: Optional[str] = Header(default=None, min_length=8, max_length=100),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Builds the trip's itinerary from live data: destination verified by
    geocoding, hotels/activities from Amadeus (when a `city_code` is given),
    a real weather forecast where one exists, prices converted with real
    exchange rates. The AI drafts; the system verifies — provider items keep
    the provider's name, price and coordinates, the AI's own suggestions are
    marked `estimated`, and a plan is saved only if it passes validation
    (clashes, impossible travel, budget, dates, weather).

    In production this returns **202** with a job to poll; in development it
    runs inline and returns the finished job."""
    job = await PlanningService(db).start(
        trip_id=trip_id, user=current_user, request=payload, idempotency_key=idempotency_key
    )
    if job["status"] in ("queued", "running"):
        response.status_code = status.HTTP_202_ACCEPTED
    return GenerationJobResponse(**job)


@router.get("/{trip_id}/generate/{job_id}", response_model=GenerationJobResponse)
async def get_generation_job(
    trip_id: uuid.UUID,
    job_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    return GenerationJobResponse(**await PlanningService(db).get_job(trip_id=trip_id, job_id=job_id, user=current_user))


@router.post(
    "/{trip_id}/generate/{job_id}/cancel", response_model=GenerationJobResponse,
    dependencies=[Depends(rate_limit(bucket="planning:cancel", max_requests=30, window_seconds=3600, per="user"))],
)
async def cancel_generation_job(
    trip_id: uuid.UUID,
    job_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Stop a queued or running generation. The trip is left unchanged (nothing is saved until the last step). Returns
    the job: `cancelled`, or `succeeded`/`failed` if it had already finished. Idempotent."""
    return GenerationJobResponse(**await PlanningService(db).cancel_job(trip_id=trip_id, job_id=job_id, user=current_user))


@router.get("/{trip_id}/preferences", response_model=TripPreferencesResponse)
async def get_trip_preferences(
    trip_id: uuid.UUID, current_user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)
):
    prefs = await PlanningService(db).get_preferences(trip_id=trip_id, user=current_user)
    return TripPreferencesResponse.model_validate(prefs) if prefs else TripPreferencesResponse()


@router.put("/{trip_id}/preferences", response_model=TripPreferencesResponse, dependencies=[Depends(require_feature(FeatureFlag.PLANNER))])
async def update_trip_preferences(
    trip_id: uuid.UUID,
    payload: TripPreferencesPayload,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    prefs = await PlanningService(db).save_preferences(trip_id=trip_id, user=current_user, payload=payload)
    return TripPreferencesResponse.model_validate(prefs)
