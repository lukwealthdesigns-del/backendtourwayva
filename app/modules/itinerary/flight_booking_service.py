"""
FlightBookingService — adds a specific, already-searched flight offer to a trip day
(Master Prompt §21: flights planning).

Before persisting the item, this now attempts to RE-VERIFY the offer live
with Amadeus's Flight Offers Price API (`AmadeusFlightProvider.reprice_offer`),
using the raw offer object `search_flights` cached moments ago (see
app/services/cache_service.flight_offer_raw_cache_key). If that succeeds,
the item is saved with the provider-confirmed price/currency and
`source="provider"`. If it can't be confirmed — the raw offer fell out of
its short cache window, or Amadeus couldn't price it — the item is still
saved (declining to add a flight the user just picked, when Amadeus is
merely slow, would be worse), but honestly marked `source="client_snapshot"`
and the response says so, rather than silently claiming a live guarantee
that was never actually checked (Blueprint §2, §108).
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import time as time_cls
from typing import Optional

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.constants import TripItemType
from app.core.exceptions import NotFoundError, ValidationAppError
from app.core.logging import get_logger
from app.modules.itinerary.flight_booking import FlightOffer, FlightOfferError, build_flight_item, validate_offer
from app.modules.itinerary.service import ItineraryService
from app.modules.trips.service import TripService
from app.providers.flights.amadeus_provider import AmadeusFlightProvider
from app.providers.flights.interface import FlightProvider
from app.repositories.trip_repository import TripRepository

logger = get_logger(__name__)


@dataclass
class FlightBookingResult:
    item_id: uuid.UUID
    title: str
    estimated_cost: float
    currency: str
    version_number: int
    price_verified: bool
    verification_note: Optional[str] = None


def _parse_hhmm(value: str) -> time_cls:
    hour, minute = value.split(":")
    return time_cls(int(hour), int(minute))


class FlightBookingService:
    def __init__(self, db: AsyncSession, flight_provider: Optional[FlightProvider] = None):
        self.db = db
        self.repo = TripRepository(db)
        self._flight_provider = flight_provider or AmadeusFlightProvider()

    async def add_to_trip(
        self, *, trip_id: uuid.UUID, day_id: uuid.UUID, offer: FlightOffer, user_id: uuid.UUID
    ) -> FlightBookingResult:
        await TripService(self.db).get_trip_authorized(trip_id=trip_id, user_id=user_id, require_editor=True)

        day = await self.repo.get_day(day_id)
        if day is None or day.trip_id != trip_id:
            raise NotFoundError("Trip day not found.")

        try:
            validate_offer(offer, day_date=day.date)
        except FlightOfferError as exc:
            raise ValidationAppError(str(exc)) from exc

        offer, price_verified = await self._reverify(offer)

        try:
            plan = build_flight_item(offer)
        except FlightOfferError as exc:
            raise ValidationAppError(str(exc)) from exc

        from app.db.models.trip import TripItem

        existing_items = await self.repo.list_items_for_day(day.id)
        next_sort_order = max((i.sort_order for i in existing_items), default=-1) + 1

        verification_note = None if price_verified else (
            "Price and availability could not be re-confirmed with the airline at add-time; "
            "shown values are from your search a moment ago and may have changed."
        )
        notes = plan.notes if price_verified else f"{plan.notes} (unverified — {verification_note})" if plan.notes else verification_note

        item = TripItem(
            trip_day_id=day.id, item_type=TripItemType.FLIGHT, title=plan.title, description=None,
            location_name=f"{offer.origin} \u2192 {offer.destination}",
            start_time=_parse_hhmm(plan.start_time) if plan.start_time else None,
            end_time=_parse_hhmm(plan.end_time) if plan.end_time else None,
            estimated_cost=plan.estimated_cost, currency=plan.currency, provider=plan.provider,
            sort_order=next_sort_order, notes=notes,
            source="provider" if price_verified else "client_snapshot",
            external_id=plan.external_id, booking_link=None,
        )
        await self.repo.add_item(item)

        version = await ItineraryService(self.db)._snapshot_version(
            trip_id=trip_id, actor_id=user_id, change_summary=f"Added flight {offer.origin}\u2192{offer.destination}.",
        )
        await self.db.commit()

        return FlightBookingResult(
            item_id=item.id, title=item.title, estimated_cost=item.estimated_cost, currency=item.currency,
            version_number=version.version_number, price_verified=price_verified, verification_note=verification_note,
        )

    async def _reverify(self, offer: FlightOffer) -> tuple[FlightOffer, bool]:
        """Attempts a live reprice via Amadeus's Flight Offers Price API.
        Returns the (possibly price-updated) offer and whether it was
        actually confirmed live. Never raises — an inability to verify
        degrades to `price_verified=False`, it never blocks adding the
        flight (Blueprint §78 graceful degradation)."""
        try:
            repriced = await self._flight_provider.reprice_offer(offer_id=offer.offer_id)
        except Exception as exc:  # noqa: BLE001
            logger.warning("flight_reprice_unexpected_error", offer_id=offer.offer_id, error=str(exc))
            return offer, False

        if repriced is None:
            return offer, False

        # Prefer the domain-layer offer passed in for everything except the
        # figures Amadeus just re-confirmed — the provider-layer FlightOffer
        # doesn't carry stops/airline_codes/cabin in a way worth reshaping here.
        confirmed = FlightOffer(
            offer_id=offer.offer_id, origin=offer.origin, destination=offer.destination,
            departure_time=offer.departure_time, arrival_time=offer.arrival_time,
            duration_iso8601=offer.duration_iso8601, stops=offer.stops, airline_codes=offer.airline_codes,
            cabin=offer.cabin, price_total=repriced.price_total, currency=repriced.currency,
            provider=offer.provider,
        )
        return confirmed, True
