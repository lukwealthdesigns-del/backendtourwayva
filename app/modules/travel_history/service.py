"""
TravelHistoryService (Master Blueprint §45).

`mark_trip_completed` is an explicit action (owner-only) — a trip
isn't history just because its end_date passed; someone has to say
the trip actually happened. When marked, it snapshots a
VisitedDestination + one VisitedPlace per itinerary item for EVERY
current trip member (not just the person who clicked complete),
since travel history is personal to each participant.

`find_last_trip_to` is the concrete answer to Blueprint §45's own
example: "the backend should query structured travel-history records
before asking the LLM to reason" — this is that query, and it's also
exposed as a Companion tool (see app/modules/companion/tools.py).
"""
from __future__ import annotations

import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.constants import TripStatus
from app.core.exceptions import NotFoundError, ValidationAppError
from app.db.models.travel_history import VisitedDestination, VisitedPlace
from app.modules.itinerary.service import ItineraryService
from app.modules.trips.service import TripService
from app.repositories.travel_history_repository import TravelHistoryRepository


class TravelHistoryService:
    def __init__(self, db: AsyncSession):
        self.db = db
        self.repo = TravelHistoryRepository(db)
        self.trip_service = TripService(db)
        self.itinerary_service = ItineraryService(db)

    async def mark_trip_completed(self, *, trip_id: uuid.UUID, actor_id: uuid.UUID) -> None:
        trip = await self.trip_service.get_trip_authorized(trip_id=trip_id, user_id=actor_id, require_editor=True)

        if trip.status == TripStatus.COMPLETED:
            raise ValidationAppError("This trip is already marked completed.")

        trip.status = TripStatus.COMPLETED
        await self.trip_service.repo.save_trip(trip)

        members = await self.trip_service.repo.list_members(trip_id)
        days = await self.itinerary_service.get_full_itinerary(trip_id)

        for member in members:
            await self.repo.create_destination(
                VisitedDestination(
                    user_id=member.user_id, trip_id=trip_id, destination=trip.destination,
                    start_date=trip.start_date, end_date=trip.end_date,
                )
            )
            for day in days:
                for item in getattr(day, "items", []):
                    await self.repo.create_place(
                        VisitedPlace(
                            user_id=member.user_id, trip_id=trip_id, place_name=item.title,
                            category=item.item_type.value, visited_date=day.date,
                            estimated_cost=item.estimated_cost, currency=item.currency,
                        )
                    )

        await self.db.commit()

    async def get_my_history(self, user_id: uuid.UUID):
        return await self.repo.list_destinations_for_user(user_id)

    async def get_my_visited_places(self, user_id: uuid.UUID):
        return await self.repo.list_all_places_for_user(user_id)

    async def find_last_trip_to(self, *, user_id: uuid.UUID, destination_query: str) -> VisitedDestination:
        result = await self.repo.find_last_trip_to(user_id, destination_query)
        if result is None:
            raise NotFoundError(f"No completed trip to '{destination_query}' found in your travel history.")
        return result
