"""
Pure logic for hotel replacement (Master Prompt §21: "hotel replacement with
recalculation"). No framework imports — testable without a database.

Recalculation means: the new item's cost is the LIVE offer's price (converted to the
trip's own currency with a real rate — the offer's own currency is never assumed to
match), never the old item's cost carried over, and the trip-wide cost delta is reported
so a client can show "this saves you $120" without re-summing every item itself.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional


@dataclass(frozen=True)
class HotelOffer:
    hotel_id: str
    hotel_name: str
    room_description: Optional[str]
    price_total: float
    currency: str
    provider: str
    latitude: Optional[float] = None
    longitude: Optional[float] = None


@dataclass(frozen=True)
class ReplacementPlan:
    title: str
    description: Optional[str]
    estimated_cost: float
    currency: str
    provider: str
    external_id: str
    latitude: Optional[float]
    longitude: Optional[float]
    image_url: Optional[str]


def convert_offer_cost(offer: HotelOffer, *, trip_currency: str, rate: Optional[float]) -> Optional[float]:
    """`rate` is 1 unit of the offer's currency expressed in `trip_currency`. None (rate
    unavailable) means the cost cannot be honestly stated — never guess an exchange rate."""
    if offer.currency.upper() == trip_currency.upper():
        return round(offer.price_total, 2)
    if rate is None:
        return None
    return round(offer.price_total * rate, 2)


def build_replacement(
    offer: HotelOffer, *, trip_currency: str, rate: Optional[float], image_url: Optional[str] = None
) -> ReplacementPlan:
    cost = convert_offer_cost(offer, trip_currency=trip_currency, rate=rate)
    return ReplacementPlan(
        title=offer.hotel_name,
        description=offer.room_description,
        estimated_cost=cost if cost is not None else 0.0,
        currency=trip_currency if cost is not None else offer.currency,
        provider=offer.provider,
        external_id=offer.hotel_id,
        latitude=offer.latitude,
        longitude=offer.longitude,
        image_url=image_url,
    )


def cost_delta(*, before: Optional[float], after: Optional[float]) -> Optional[float]:
    """Positive = the replacement costs MORE. None when either side is unknown — never
    fabricate a saving/loss figure from an unpriced item."""
    if before is None or after is None:
        return None
    return round(after - before, 2)
