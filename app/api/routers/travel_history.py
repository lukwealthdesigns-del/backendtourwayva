"""Travel history endpoints (Master Blueprint §45)."""
from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies import get_current_user
from app.db.models.user import User
from app.db.session import get_db
from app.modules.travel_history.schemas import VisitedDestinationResponse, VisitedPlaceResponse
from app.modules.travel_history.service import TravelHistoryService

router = APIRouter(tags=["Travel History"])


@router.post("/trips/{trip_id}/complete", status_code=status.HTTP_204_NO_CONTENT)
async def mark_trip_completed(
    trip_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Snapshots this trip into every current member's travel history
    (Blueprint §45). Requires editor/owner access."""
    await TravelHistoryService(db).mark_trip_completed(trip_id=trip_id, actor_id=current_user.id)


@router.get("/travel-history", response_model=list[VisitedDestinationResponse])
async def get_my_travel_history(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    destinations = await TravelHistoryService(db).get_my_history(current_user.id)
    return [VisitedDestinationResponse.model_validate(d) for d in destinations]


@router.get("/travel-history/places", response_model=list[VisitedPlaceResponse])
async def get_my_visited_places(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    places = await TravelHistoryService(db).get_my_visited_places(current_user.id)
    return [VisitedPlaceResponse.model_validate(p) for p in places]


@router.get("/travel-history/search", response_model=VisitedDestinationResponse)
async def find_last_trip_to(
    destination: str = Query(..., min_length=1, max_length=200),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Answers 'What was my last trip to X?' by querying structured
    travel history directly (Blueprint §45) — not by asking the LLM."""
    result = await TravelHistoryService(db).find_last_trip_to(user_id=current_user.id, destination_query=destination)
    return VisitedDestinationResponse.model_validate(result)
