"""
Trip + itinerary endpoints (Master Blueprint §13-19).

Every mutating/reading endpoint below re-verifies trip
ownership/membership server-side via TripService.get_trip_authorized
— the trip_id in the URL is never trusted on its own (Principle 1:
never trust the frontend).
"""
from __future__ import annotations

import uuid
from typing import Literal

from fastapi import APIRouter, Depends, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies import get_current_user, require_feature
from app.modules.currency.budget_policy import ensure_budget_is_realistic
from app.core.constants import FeatureFlag
from app.core.exceptions import NotFoundError
from app.db.models.user import User
from app.db.session import get_db
from app.modules.itinerary.schemas import (
    ItineraryValidationResult,
    TripDayResponse,
    TripItemCreateRequest,
    TripItemResponse,
    TripItemUpdateRequest,
    TripVersionResponse,
)
from app.modules.itinerary.flight_booking import FlightOffer
from app.modules.itinerary.flight_booking_service import FlightBookingService
from app.modules.itinerary.hotel_replacement_service import HotelReplacementService
from app.modules.itinerary.service import ItineraryService
from app.modules.trips.schemas import (
    TripUpdateRequest,
    AddFlightRequest,
    AddFlightResponse,
    BookingClickRequest,
    BookingClickResponse,
    ReplaceHotelRequest,
    ReplaceHotelResponse,
    TripCreateRequest,
    TripResponse,
)
from app.modules.trips.display_status import in_bucket
from app.modules.trips.presenters import trip_response, trip_responses
from app.modules.trips.service import TripService

router = APIRouter(prefix="/trips", tags=["Trips"])


class TripItemWriteResponse(TripItemResponse):
    validation: ItineraryValidationResult


@router.patch("/{trip_id}", response_model=TripResponse, dependencies=[Depends(require_feature(FeatureFlag.PLANNER))])
async def update_trip(
    trip_id: uuid.UUID,
    payload: TripUpdateRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Edit a trip's basics (owner/editor): title, origin, travelers, budget always; destination and dates only while the
    trip has no itinerary and is not being generated (409 `itinerary_exists` / generation-in-progress otherwise)."""
    if payload.budget_amount is not None:
        await ensure_budget_is_realistic(payload.budget_amount, payload.budget_currency or (await TripService(db).get_trip_authorized(trip_id=trip_id, user_id=current_user.id)).budget_currency)
    service = TripService(db)
    trip = await service.update_trip(trip_id=trip_id, user_id=current_user.id, payload=payload)
    membership = await service.repo.get_membership(trip.id, current_user.id)
    return await trip_response(trip, membership=membership, user=current_user)


@router.post("", response_model=TripResponse, status_code=status.HTTP_201_CREATED, dependencies=[Depends(require_feature(FeatureFlag.PLANNER))])
async def create_trip(
    payload: TripCreateRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Creates the trip, makes the caller its owner, and scaffolds one
    empty TripDay per calendar day in the date range."""
    await ensure_budget_is_realistic(payload.budget_amount, payload.budget_currency)
    service = TripService(db)
    trip = await service.create_trip(owner_id=current_user.id, payload=payload)
    membership = await service.repo.get_membership(trip.id, current_user.id)
    return await trip_response(trip, membership=membership, user=current_user)


@router.get("", response_model=list[TripResponse])
async def list_my_trips(
    bucket: Literal["all", "drafts", "upcoming", "active", "completed", "archived"] = Query(
        default="all",
        description="Trips-page tab. `all` = every trip you have not archived; `archived` = only the ones you did.",
    ),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Your trips, newest start date first. Each carries the backend-computed `display_status`,
    `bucket`, `is_archived` and (while generating / after a failure) `generation`."""
    pairs = await TripService(db).list_trips_with_membership(current_user.id)
    responses = await trip_responses(pairs, user=current_user)
    return [r for r in responses if in_bucket(r.display_status, bucket)]


@router.get("/{trip_id}", response_model=TripResponse)
async def get_trip(
    trip_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    service = TripService(db)
    trip = await service.get_trip_authorized(trip_id=trip_id, user_id=current_user.id)
    membership = await service.repo.get_membership(trip_id, current_user.id)
    return await trip_response(trip, membership=membership, user=current_user)


@router.post("/{trip_id}/archive", response_model=TripResponse)
async def archive_trip(
    trip_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Archive this trip for YOU (other members still see it). Idempotent. Any member may do it."""
    trip, membership = await TripService(db).set_archived(trip_id=trip_id, user_id=current_user.id, archived=True)
    return await trip_response(trip, membership=membership, user=current_user)


@router.post("/{trip_id}/unarchive", response_model=TripResponse)
async def unarchive_trip(
    trip_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Bring an archived trip back into your lists. Idempotent."""
    trip, membership = await TripService(db).set_archived(trip_id=trip_id, user_id=current_user.id, archived=False)
    return await trip_response(trip, membership=membership, user=current_user)


@router.get("/{trip_id}/itinerary", response_model=list[TripDayResponse])
async def get_itinerary(
    trip_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Full structured itinerary: every day, with its items, in order."""
    await TripService(db).get_trip_authorized(trip_id=trip_id, user_id=current_user.id)
    days = await ItineraryService(db).get_full_itinerary(trip_id)
    return [TripDayResponse.model_validate(d) for d in days]


@router.post(
    "/days/{day_id}/items", response_model=TripItemWriteResponse, status_code=status.HTTP_201_CREATED, dependencies=[Depends(require_feature(FeatureFlag.PLANNER))]
)
async def add_trip_item(
    day_id: uuid.UUID,
    payload: TripItemCreateRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Adds an item to a trip day. Requires editor (or owner) role.
    Runs itinerary validation immediately after the write and returns
    any issues found — see app/modules/itinerary/validation_service.py."""
    trip_service = TripService(db)
    day = await trip_service.get_trip_day_authorized(day_id=day_id, user_id=current_user.id, require_editor=True)

    item, validation = await ItineraryService(db).add_item(day=day, payload=payload, actor_id=current_user.id)
    return TripItemWriteResponse(**TripItemResponse.model_validate(item).model_dump(), validation=validation)


@router.patch("/items/{item_id}", response_model=TripItemWriteResponse, dependencies=[Depends(require_feature(FeatureFlag.PLANNER))])
async def update_trip_item(
    item_id: uuid.UUID,
    payload: TripItemUpdateRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    itinerary_service = ItineraryService(db)
    item = await itinerary_service.repo.get_item(item_id)
    if item is None:
        raise NotFoundError("Trip item not found.")

    await TripService(db).get_trip_day_authorized(day_id=item.trip_day_id, user_id=current_user.id, require_editor=True)

    updated_item, validation = await itinerary_service.update_item(
        item=item, payload=payload, actor_id=current_user.id
    )
    return TripItemWriteResponse(**TripItemResponse.model_validate(updated_item).model_dump(), validation=validation)


@router.post(
    "/{trip_id}/flights", response_model=AddFlightResponse, status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(require_feature(FeatureFlag.PLANNER))],
)
async def add_flight(
    trip_id: uuid.UUID,
    payload: AddFlightRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Adds a SPECIFIC flight offer (from a `POST /flights/search` result the user just
    chose) to a trip day. Before saving, attempts to re-confirm the price/availability
    live with Amadeus (Flight Offers Price); if that succeeds the item is marked
    verified, otherwise it's still added — using the just-searched snapshot — but
    honestly marked unverified in the response (see AddFlightResponse.price_verified)."""
    offer = FlightOffer(
        offer_id=payload.offer_id, origin=payload.origin.upper(), destination=payload.destination.upper(),
        departure_time=payload.departure_time, arrival_time=payload.arrival_time,
        duration_iso8601=payload.duration_iso8601, stops=payload.stops, airline_codes=payload.airline_codes,
        price_total=payload.price_total, currency=payload.currency.upper(), provider=payload.provider,
        cabin=payload.cabin,
    )
    result = await FlightBookingService(db).add_to_trip(
        trip_id=trip_id, day_id=payload.day_id, offer=offer, user_id=current_user.id
    )
    return AddFlightResponse(
        item_id=result.item_id, title=result.title, estimated_cost=result.estimated_cost,
        currency=result.currency, version_number=result.version_number,
        price_verified=result.price_verified, verification_note=result.verification_note,
    )


@router.put(
    "/items/{item_id}/replace-hotel", response_model=ReplaceHotelResponse,
    dependencies=[Depends(require_feature(FeatureFlag.PLANNER))],
)
async def replace_hotel(
    item_id: uuid.UUID,
    payload: ReplaceHotelRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Deterministically swaps a HOTEL item for a specific, freshly re-priced hotel offer
    (`new_hotel_id` from a `POST /hotels/search` result) — no AI call, since picking a
    search result is a well-defined operation. Recalculates cost in the trip's own
    currency and creates a new itinerary version. For a natural-language request
    ("find me a cheaper hotel"), use the Companion instead."""
    itinerary_service = ItineraryService(db)
    item = await itinerary_service.repo.get_item(item_id)
    if item is None:
        raise NotFoundError("Trip item not found.")
    day = await itinerary_service.repo.get_day(item.trip_day_id)
    if day is None:
        raise NotFoundError("Trip item not found.")

    result = await HotelReplacementService(db).replace(
        trip_id=day.trip_id, item_id=item_id, new_hotel_id=payload.new_hotel_id, user_id=current_user.id
    )
    return ReplaceHotelResponse(
        item_id=result.item_id, title=result.title, previous_cost=result.previous_cost, new_cost=result.new_cost,
        currency=result.currency, cost_delta=result.cost_delta, version_number=result.version_number,
    )


@router.delete("/items/{item_id}", status_code=status.HTTP_204_NO_CONTENT, dependencies=[Depends(require_feature(FeatureFlag.PLANNER))])
async def delete_trip_item(
    item_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    itinerary_service = ItineraryService(db)
    item = await itinerary_service.repo.get_item(item_id)
    if item is None:
        raise NotFoundError("Trip item not found.")

    await TripService(db).get_trip_day_authorized(day_id=item.trip_day_id, user_id=current_user.id, require_editor=True)
    await itinerary_service.delete_item(item=item, actor_id=current_user.id)


@router.post("/items/{item_id}/booking-click", response_model=BookingClickResponse)
async def record_booking_click(
    item_id: uuid.UUID,
    payload: BookingClickRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Call this when the user follows a trip item's `booking_link` out to
    the provider. Any trip member (viewer included) may record a click —
    it never modifies the trip, just logs intent for the admin dashboard's
    ESTIMATED affiliate-revenue reporting (Blueprint §57). A booking_link-less
    item (e.g. a manually-added note) simply has nothing to click through to
    at the client, so this endpoint doesn't require one server-side."""
    item = await ItineraryService(db).repo.get_item(item_id)
    if item is None:
        raise NotFoundError("Trip item not found.")

    day = await TripService(db).get_trip_day_authorized(day_id=item.trip_day_id, user_id=current_user.id)

    from app.modules.analytics.service import AnalyticsService

    await AnalyticsService(db).record_booking_click(
        user_id=current_user.id, trip_id=day.trip_id, trip_item_id=item.id,
        item_type=item.item_type.value, provider=item.provider or "unknown",
        estimated_value=item.estimated_cost or 0.0, estimated_value_currency=item.currency or "USD",
        estimated_value_usd=payload.estimated_value_usd,
    )
    return BookingClickResponse()


@router.get("/{trip_id}/versions", response_model=list[TripVersionResponse])
async def list_trip_versions(
    trip_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    await TripService(db).get_trip_authorized(trip_id=trip_id, user_id=current_user.id)
    versions = await ItineraryService(db).list_versions(trip_id)
    return [TripVersionResponse.model_validate(v) for v in versions]


@router.post("/{trip_id}/versions/{version_id}/restore", response_model=TripVersionResponse, dependencies=[Depends(require_feature(FeatureFlag.PLANNER))])
async def restore_trip_version(
    trip_id: uuid.UUID,
    version_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Restores a prior version's days/items and records the
    restoration itself as a new version (history is append-only)."""
    trip = await TripService(db).get_trip_authorized(trip_id=trip_id, user_id=current_user.id, require_editor=True)
    new_version = await ItineraryService(db).restore_version(
        trip=trip, version_id=version_id, actor_id=current_user.id
    )
    return TripVersionResponse.model_validate(new_version)
