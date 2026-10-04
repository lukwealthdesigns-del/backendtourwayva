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
    price_total: float
    currency: str
    provider: str


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
