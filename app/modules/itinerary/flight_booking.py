"""
Pure logic for adding a flight to a trip (Master Prompt §21: "flights not planned").

This module handles what's DETERMINISTIC and offer-shape-only: the offer is
structurally sound (arrival after departure, a positive price, known
airline/stop count) and its departure date falls on the trip day it is being
attached to — catching a client bug or a stale/mismatched offer before it
reaches the itinerary. Live re-verification against Amadeus (repricing the
offer to confirm it's still bookable at the stated price) is NOT done here —
see FlightBookingService._reverify, which owns that because it needs the
provider and the Redis-cached raw offer object, neither of which belongs in
a pure-function module.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from typing import Optional


class FlightOfferError(ValueError):
    """The offer is malformed or does not match the day it is being attached to."""


@dataclass(frozen=True)
class FlightOffer:
    offer_id: str
    origin: str
    destination: str
    departure_time: str    # ISO 8601, e.g. "2026-10-01T08:30:00"
    arrival_time: str
    duration_iso8601: str
    stops: int
    airline_codes: list[str]
    price_total: float
    currency: str
    provider: str
    cabin: Optional[str] = None


@dataclass(frozen=True)
class FlightItemPlan:
    title: str
    notes: str
    start_time: Optional[str]   # "HH:MM", None if arrival is on a different calendar day
    end_time: Optional[str]
    estimated_cost: float
    currency: str
    provider: str
    external_id: str


def _parse(value: str, field: str) -> datetime:
    try:
        return datetime.fromisoformat(value)
    except (ValueError, TypeError) as exc:
        raise FlightOfferError(f"Invalid {field}.") from exc


def validate_offer(offer: FlightOffer, *, day_date: date) -> None:
    departure = _parse(offer.departure_time, "departure_time")
    arrival = _parse(offer.arrival_time, "arrival_time")
    if arrival <= departure:
        raise FlightOfferError("Arrival time must be after departure time.")
    if offer.price_total <= 0:
        raise FlightOfferError("Flight price must be positive.")
    if len(offer.origin) != 3 or len(offer.destination) != 3:
        raise FlightOfferError("Origin and destination must be 3-letter IATA codes.")
    if departure.date() != day_date:
        raise FlightOfferError(
            f"This flight departs on {departure.date().isoformat()}, not the selected day ({day_date.isoformat()})."
        )


def build_flight_item(offer: FlightOffer) -> FlightItemPlan:
    departure, arrival = _parse(offer.departure_time, "departure_time"), _parse(offer.arrival_time, "arrival_time")
    same_day = departure.date() == arrival.date()
    stops_label = "Direct" if offer.stops == 0 else f"{offer.stops} stop{'s' if offer.stops != 1 else ''}"
    airlines = "/".join(offer.airline_codes) or "Unknown carrier"
    return FlightItemPlan(
        title=f"Flight {offer.origin} \u2192 {offer.destination} ({airlines})",
        notes=f"{stops_label} \u00b7 {offer.duration_iso8601}" + ("" if offer.cabin is None else f" \u00b7 {offer.cabin}"),
        start_time=departure.strftime("%H:%M"),
        end_time=arrival.strftime("%H:%M") if same_day else None,
        estimated_cost=round(offer.price_total, 2),
        currency=offer.currency.upper(),
        provider=offer.provider,
        external_id=offer.offer_id,
    )
