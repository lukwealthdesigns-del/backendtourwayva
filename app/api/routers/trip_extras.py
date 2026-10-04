"""Trip notes/costs/routes endpoints — behavior for the
previously-schema-only trip_notes, trip_costs, trip_routes tables."""
from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies import get_current_user, require_feature
from app.core.constants import FeatureFlag
from app.db.models.user import User
from app.db.session import get_db
from app.modules.trips.extras_schemas import (
    TripCostCreateRequest,
    TripCostResponse,
    TripCostSummaryResponse,
    TripNoteCreateRequest,
    TripNoteResponse,
    TripRouteRequest,
    TripRouteResponse,
)
from app.modules.trips.extras_service import TripExtrasService

router = APIRouter(prefix="/trips", tags=["Trip Extras"])


# --- PDF export ---

@router.post("/{trip_id}/export-pdf", dependencies=[Depends(require_feature(FeatureFlag.PDF_EXPORT))])
async def export_trip_pdf(
    trip_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Generates a real PDF of the trip's itinerary and uploads it to
    Supabase Storage (Blueprint's Trip PDFs use case). Returns the
    resulting Attachment record, including its download URL."""
    from app.modules.attachments.schemas import AttachmentResponse
    from app.modules.trips.pdf_service import TripPDFService

    attachment = await TripPDFService(db).generate_and_upload(trip_id=trip_id, user_id=current_user.id)
    return AttachmentResponse.model_validate(attachment)


# --- Notes ---

@router.post("/{trip_id}/notes", response_model=TripNoteResponse, status_code=status.HTTP_201_CREATED, dependencies=[Depends(require_feature(FeatureFlag.PLANNER))])
async def add_trip_note(
    trip_id: uuid.UUID,
    payload: TripNoteCreateRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    note = await TripExtrasService(db).add_note(trip_id=trip_id, user_id=current_user.id, content=payload.content)
    return TripNoteResponse.model_validate(note)


@router.get("/{trip_id}/notes", response_model=list[TripNoteResponse])
async def list_trip_notes(
    trip_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    notes = await TripExtrasService(db).list_notes(trip_id=trip_id, user_id=current_user.id)
    return [TripNoteResponse.model_validate(n) for n in notes]


@router.delete("/notes/{note_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_trip_note(
    note_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    await TripExtrasService(db).delete_note(note_id=note_id, actor_id=current_user.id)


# --- Costs ---

@router.post("/{trip_id}/costs", response_model=TripCostResponse, status_code=status.HTTP_201_CREATED, dependencies=[Depends(require_feature(FeatureFlag.PLANNER))])
async def add_trip_cost(
    trip_id: uuid.UUID,
    payload: TripCostCreateRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    cost = await TripExtrasService(db).add_cost(trip_id=trip_id, user_id=current_user.id, payload=payload)
    return TripCostResponse.model_validate(cost)


@router.get("/{trip_id}/costs", response_model=list[TripCostResponse])
async def list_trip_costs(
    trip_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    costs = await TripExtrasService(db).list_costs(trip_id=trip_id, user_id=current_user.id)
    return [TripCostResponse.model_validate(c) for c in costs]


@router.get("/{trip_id}/costs/summary", response_model=TripCostSummaryResponse)
async def get_trip_cost_summary(
    trip_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    return await TripExtrasService(db).get_cost_summary(trip_id=trip_id, user_id=current_user.id)


@router.delete("/costs/{cost_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_trip_cost(
    cost_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    await TripExtrasService(db).delete_cost(cost_id=cost_id, actor_id=current_user.id)


# --- Routes ---

@router.post("/{trip_id}/routes", response_model=TripRouteResponse, dependencies=[Depends(require_feature(FeatureFlag.PLANNER))])
async def calculate_trip_route(
    trip_id: uuid.UUID,
    payload: TripRouteRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Cache-first: returns a stored TripRoute if this exact
    (origin, destination, mode) has been calculated before; otherwise
    calls MapsService and caches the result."""
    route, source = await TripExtrasService(db).get_or_calculate_route(
        trip_id=trip_id, user_id=current_user.id,
        origin_item_id=payload.origin_item_id, destination_item_id=payload.destination_item_id,
        mode=payload.mode,
    )
    return TripRouteResponse(
        trip_id=route.trip_id, origin_item_id=route.origin_item_id, destination_item_id=route.destination_item_id,
        mode=route.mode, distance_meters=route.distance_meters, duration_seconds=route.duration_seconds,
        source=source,
    )


@router.get("/{trip_id}/routes", response_model=list[TripRouteResponse])
async def list_trip_routes(
    trip_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    routes = await TripExtrasService(db).list_routes(trip_id=trip_id, user_id=current_user.id)
    return [
        TripRouteResponse(
            trip_id=r.trip_id, origin_item_id=r.origin_item_id, destination_item_id=r.destination_item_id,
            mode=r.mode, distance_meters=r.distance_meters, duration_seconds=r.duration_seconds, source="cache",
        )
        for r in routes
    ]
