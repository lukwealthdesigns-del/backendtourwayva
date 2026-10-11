"""Hotel provider abstraction (Master Blueprint §20-22)."""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import date
from typing import Optional


@dataclass(frozen=True)
class HotelOffer:
    hotel_id: str
    hotel_name: str
    offer_id: str
    latitude: Optional[float]
    longitude: Optional[float]
    check_in: date
    check_out: date
    room_description: Optional[str]
    price_total: Optional[float]      # None = no verified price (discovery results never carry one)
    currency: Optional[str]
    provider: str
    # Discovery / provenance. The defaults keep every inventory (Amadeus) construction valid.
    kind: str = "inventory"           # "inventory" = live rooms and prices; "discovery" = the place exists, nothing more
    provider_reference: Optional[str] = None
    address: Optional[str] = None
    rating: Optional[float] = None
    user_rating_count: Optional[int] = None
    photo_url: Optional[str] = None
    website: Optional[str] = None
    maps_url: Optional[str] = None
    price_level: Optional[str] = None  # "$".."$$$$": a qualitative hint from the provider, NEVER an amount


class HotelDiscoveryProvider(ABC):
    """Finds which hotels exist near a place (name, location, rating, photos). It is NOT inventory: it never knows
    rooms, availability or prices, and callers must never present its results as bookable offers."""

    @abstractmethod
    async def discover_hotels(
        self, *, latitude: float, longitude: float, destination: str, check_in: Optional[date] = None,
        check_out: Optional[date] = None, max_hotels: int = 20,
    ) -> list[HotelOffer]:
        raise NotImplementedError

    @abstractmethod
    async def get_hotel(self, *, reference: str) -> Optional[HotelOffer]:
        raise NotImplementedError


class HotelProvider(ABC):
    @abstractmethod
    async def search_hotels(
        self,
        *,
        city_code: str,
        check_in: date,
        check_out: date,
        adults: int,
        max_hotels: int = 20,
    ) -> list[HotelOffer]:
        """Search hotel offers for a city (IATA city code, e.g. 'PAR')
        and date range. Never fabricate availability or prices — an
        empty list means no offers were found, not an error."""
        raise NotImplementedError
