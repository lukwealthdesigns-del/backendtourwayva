"""
TripExtrasService — real behavior behind trip_notes, trip_costs, and
trip_routes (previously schema-only models).

Notes and costs follow the same authorization pattern as
TripComment: any member can add, author-or-owner can delete. Routes
are cache-first (Blueprint §3 "reuse before regenerate") over
MapsService, keyed on (trip, origin item, destination item, mode) —
a route between two fixed points doesn't change often, so once
calculated it's reused rather than re-hitting the routing provider
every time the itinerary is viewed.
"""
from __future__ import annotations

import uuid
from collections import defaultdict

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.constants import TripMemberRole
from app.core.exceptions import ForbiddenError, NotFoundError, ValidationAppError
from app.db.models.trip import TripCost, TripNote, TripRoute
from app.modules.maps.service import MapsService
from app.modules.trips.extras_schemas import TripCostCreateRequest, TripCostSummaryResponse
from app.modules.trips.service import TripService
from app.providers.maps.interface import TravelMode
from app.repositories.trip_extras_repository import TripExtrasRepository


class TripExtrasService:
    def __init__(self, db: AsyncSession):
        self.db = db
        self.repo = TripExtrasRepository(db)
        self.trip_service = TripService(db)

    # --- Notes ---

    async def add_note(self, *, trip_id: uuid.UUID, user_id: uuid.UUID, content: str) -> TripNote:
        await self.trip_service.get_trip_authorized(trip_id=trip_id, user_id=user_id)
        note = TripNote(trip_id=trip_id, user_id=user_id, content=content)
        await self.repo.create_note(note)
        await self.db.commit()
        return note

    async def list_notes(self, *, trip_id: uuid.UUID, user_id: uuid.UUID):
        await self.trip_service.get_trip_authorized(trip_id=trip_id, user_id=user_id)
        return await self.repo.list_notes_for_trip(trip_id)

    async def delete_note(self, *, note_id: uuid.UUID, actor_id: uuid.UUID) -> None:
        note = await self.repo.get_note(note_id)
        if note is None:
            raise NotFoundError("Note not found.")

        await self.trip_service.get_trip_authorized(trip_id=note.trip_id, user_id=actor_id)
        membership = await self.trip_service.repo.get_membership(note.trip_id, actor_id)

        is_author = note.user_id == actor_id
        is_owner = membership is not None and membership.role == TripMemberRole.OWNER
        if not (is_author or is_owner):
            raise ForbiddenError("You can only delete your own notes (or, as owner, anyone's).")

        await self.repo.delete_note(note)
        await self.db.commit()

    # --- Costs ---

    async def add_cost(self, *, trip_id: uuid.UUID, user_id: uuid.UUID, payload: TripCostCreateRequest) -> TripCost:
        await self.trip_service.get_trip_authorized(trip_id=trip_id, user_id=user_id)
        cost = TripCost(
            trip_id=trip_id, added_by=user_id, category=payload.category,
            description=payload.description, amount=payload.amount, currency=payload.currency.upper(),
        )
        await self.repo.create_cost(cost)
        await self.db.commit()
        return cost

    async def list_costs(self, *, trip_id: uuid.UUID, user_id: uuid.UUID):
        await self.trip_service.get_trip_authorized(trip_id=trip_id, user_id=user_id)
        return await self.repo.list_costs_for_trip(trip_id)

    async def delete_cost(self, *, cost_id: uuid.UUID, actor_id: uuid.UUID) -> None:
        cost = await self.repo.get_cost(cost_id)
        if cost is None:
            raise NotFoundError("Cost entry not found.")

        await self.trip_service.get_trip_authorized(trip_id=cost.trip_id, user_id=actor_id)
        membership = await self.trip_service.repo.get_membership(cost.trip_id, actor_id)

        is_author = cost.added_by == actor_id
        is_owner = membership is not None and membership.role == TripMemberRole.OWNER
        if not (is_author or is_owner):
            raise ForbiddenError("You can only delete your own cost entries (or, as owner, anyone's).")

        await self.repo.delete_cost(cost)
        await self.db.commit()

    async def get_cost_summary(self, *, trip_id: uuid.UUID, user_id: uuid.UUID) -> TripCostSummaryResponse:
        trip = await self.trip_service.get_trip_authorized(trip_id=trip_id, user_id=user_id)
        costs = await self.repo.list_costs_for_trip(trip_id)

        totals: dict[str, float] = defaultdict(float)
        for cost in costs:
            totals[cost.currency] += cost.amount

        return TripCostSummaryResponse(
            totals_by_currency=dict(totals),
            budget_amount=trip.budget_amount,
            budget_currency=trip.budget_currency,
        )

    # --- Routes (cache-first over MapsService) ---

    async def get_or_calculate_route(
        self, *, trip_id: uuid.UUID, user_id: uuid.UUID,
        origin_item_id: uuid.UUID, destination_item_id: uuid.UUID, mode: TravelMode,
    ) -> tuple[TripRoute, str]:
        await self.trip_service.get_trip_authorized(trip_id=trip_id, user_id=user_id)

        cached = await self.repo.get_cached_route(trip_id, origin_item_id, destination_item_id, mode.value)
        if cached is not None:
            return cached, "cache"

        origin_item = await self.trip_service.repo.get_item(origin_item_id)
        destination_item = await self.trip_service.repo.get_item(destination_item_id)
        if origin_item is None or destination_item is None:
            raise NotFoundError("Origin or destination trip item not found.")
        if origin_item.latitude is None or origin_item.longitude is None:
            raise ValidationAppError("Origin item has no coordinates set.")
        if destination_item.latitude is None or destination_item.longitude is None:
            raise ValidationAppError("Destination item has no coordinates set.")

        result = await MapsService().get_route(
            origin_lat=origin_item.latitude, origin_lon=origin_item.longitude,
            destination_lat=destination_item.latitude, destination_lon=destination_item.longitude,
            mode=mode,
        )

        route = TripRoute(
            trip_id=trip_id, origin_item_id=origin_item_id, destination_item_id=destination_item_id,
            mode=mode.value, distance_meters=result.distance_meters, duration_seconds=result.duration_seconds,
        )
        await self.repo.create_route(route)
        await self.db.commit()
        return route, "live"

    async def list_routes(self, *, trip_id: uuid.UUID, user_id: uuid.UUID):
        await self.trip_service.get_trip_authorized(trip_id=trip_id, user_id=user_id)
        return await self.repo.list_routes_for_trip(trip_id)
