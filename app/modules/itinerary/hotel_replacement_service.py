"""
HotelReplacementService — swaps a trip's hotel item for a specific, freshly re-priced
hotel offer (Master Prompt §21: "hotel replacement with recalculation").

Deliberately NOT routed through the AI revision workflow: picking a hotel from search
results is a well-defined, deterministic operation — fetch the live offer, verify it,
write it. No model call, so no risk of the model inventing a price or losing an
unrelated part of the itinerary. (A natural-language request like "find me a cheaper
hotel" still goes through `propose_itinerary_revision`; this is the next, mechanical step
once the user or the model has a specific hotel in mind — the Companion's
`replace_hotel` tool calls this directly, no proposal/confirmation needed, since it is
the deterministic equivalent of picking a hotel search result in the UI.)
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Optional

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.constants import TripItemType
from app.core.exceptions import NotFoundError, ValidationAppError
from app.modules.currency.service import CurrencyService
from app.modules.hotels.service import HotelService
from app.modules.images.service import ImageService
from app.modules.itinerary.hotel_replacement import HotelOffer, build_replacement, cost_delta
from app.modules.itinerary.service import ItineraryService
from app.modules.trips.service import TripService
from app.repositories.trip_repository import TripRepository


@dataclass
class ReplacementResult:
    item_id: uuid.UUID
    title: str
    previous_cost: Optional[float]
    new_cost: Optional[float]
    currency: str
    cost_delta: Optional[float]
    version_number: int


class HotelReplacementService:
    def __init__(self, db: AsyncSession, *, hotels: Optional[HotelService] = None,
                 currency: Optional[CurrencyService] = None, images: Optional[ImageService] = None):
        self.db = db
        self.repo = TripRepository(db)
        self.hotels = hotels or HotelService()
        self.currency = currency or CurrencyService()
        self.images = images or ImageService()

    async def replace(self, *, trip_id: uuid.UUID, item_id: uuid.UUID, new_hotel_id: str, user_id: uuid.UUID) -> ReplacementResult:
        trip = await TripService(self.db).get_trip_authorized(trip_id=trip_id, user_id=user_id, require_editor=True)

        item = await self.repo.get_item(item_id)
        if item is None:
            raise NotFoundError("Trip item not found.")
        day = await self.repo.get_day(item.trip_day_id)
        if day is None or day.trip_id != trip_id:
            raise NotFoundError("Trip item not found.")
        if item.item_type != TripItemType.HOTEL:
            raise ValidationAppError("This item is not a hotel and cannot be replaced with one.")

        adults = max(1, min(trip.travelers, 9))
        offer_response = await self.hotels.get_offer(
            hotel_id=new_hotel_id, check_in=trip.start_date, check_out=trip.end_date, adults=adults
        )
        if offer_response is None:
            raise ValidationAppError("That hotel has no available offer for these dates.")

        offer = HotelOffer(
            hotel_id=offer_response.hotel_id, hotel_name=offer_response.hotel_name,
            room_description=offer_response.room_description, price_total=offer_response.price_total,
            currency=offer_response.currency, provider=offer_response.provider,
            latitude=offer_response.latitude, longitude=offer_response.longitude,
        )
        trip_currency = (trip.budget_currency or item.currency or offer.currency).upper()

        rate: Optional[float] = None
        if offer.currency.upper() != trip_currency:
            try:
                rate = (await self.currency.get_rate(offer.currency, trip_currency)).rate
            except Exception:  # noqa: BLE001 - price is left unknown rather than guessed
                rate = None

        image_url = None
        try:
            found = await self.images.get_or_search(offer.hotel_name)
            image_url = found.url
        except Exception:  # noqa: BLE001 - cosmetic only; never blocks the replacement
            pass

        plan = build_replacement(offer, trip_currency=trip_currency, rate=rate, image_url=image_url)
        previous_cost = item.estimated_cost

        item.title = plan.title
        item.location_name = plan.title
        item.description = plan.description
        item.estimated_cost = plan.estimated_cost if rate is not None or offer.currency.upper() == trip_currency else None
        item.currency = trip_currency if item.estimated_cost is not None else offer.currency
        item.provider = plan.provider
        item.external_id = plan.external_id
        item.source = "provider"
        item.booking_link = None
        item.image_url = plan.image_url
        if plan.latitude is not None and plan.longitude is not None:
            item.latitude, item.longitude = plan.latitude, plan.longitude
        await self.repo.save_item(item)

        version = await ItineraryService(self.db)._snapshot_version(
            trip_id=trip_id, actor_id=user_id,
            change_summary=f"Replaced hotel with '{offer.hotel_name}'.",
        )
        await self.db.commit()

        return ReplacementResult(
            item_id=item.id, title=item.title, previous_cost=previous_cost, new_cost=item.estimated_cost,
            currency=item.currency, cost_delta=cost_delta(before=previous_cost, after=item.estimated_cost),
            version_number=version.version_number,
        )
