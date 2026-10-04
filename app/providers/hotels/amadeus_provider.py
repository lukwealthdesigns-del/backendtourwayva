"""
Amadeus hotel provider (Master Blueprint §20-22).

Amadeus's hotel search is a two-step lookup:
  1. GET /v1/reference-data/locations/hotels/by-city — hotel IDs in a city
  2. GET /v3/shopping/hotel-offers — live offers for those hotel IDs

Both calls go through the shared AmadeusClient (OAuth2 token
management). Never invents availability or prices — an empty/missing
result becomes an empty list or NotFoundError, never a guess.
"""
from __future__ import annotations

from datetime import date
from typing import Optional

from app.core.exceptions import NotFoundError
from app.core.logging import get_logger
from app.providers.amadeus.client import amadeus_client
from app.providers.hotels.interface import HotelOffer, HotelProvider

logger = get_logger(__name__)


class AmadeusHotelProvider(HotelProvider):
    async def search_hotels(
        self,
        *,
        city_code: str,
        check_in: date,
        check_out: date,
        adults: int,
        max_hotels: int = 20,
        hotel_ids: Optional[list[str]] = None,
    ) -> list[HotelOffer]:
        """`hotel_ids`, when given, queries EXACTLY those hotels (bypasses the by-city
        lookup) — used to re-price one specific hotel for replacement/re-verification."""
        if hotel_ids is None:
            hotel_ids = await self._get_hotel_ids_for_city(city_code, limit=max_hotels)
        if not hotel_ids:
            return []

        try:
            data = await amadeus_client.get(
                "/v3/shopping/hotel-offers",
                params={
                    "hotelIds": ",".join(hotel_ids),
                    "checkInDate": check_in.isoformat(),
                    "checkOutDate": check_out.isoformat(),
                    "adults": adults,
                    "bestRateOnly": "true",
                },
                resource="hotels",
            )
        except NotFoundError:
            return []

        return self._normalize_offers(data.get("data", []), check_in, check_out)

    async def resolve_city_code(self, keyword: str) -> Optional[str]:
        """IATA city code from a free-text destination name (Master Prompt §21: 'IATA
        destination -> code lookup for hotels'), via Amadeus's own city/airport search —
        so hotel search no longer silently skips a trip just because the client did not
        supply a code. Returns None (never guesses) if nothing matches."""
        try:
            data = await amadeus_client.get(
                "/v1/reference-data/locations",
                params={"keyword": keyword, "subType": "CITY", "page[limit]": 5},
                resource="hotels",
            )
        except NotFoundError:
            return None
        for entry in data.get("data", []):
            code = entry.get("iataCode")
            if code and len(code) == 3:
                return code.upper()
        return None

    async def _get_hotel_ids_for_city(self, city_code: str, *, limit: int) -> list[str]:
        try:
            data = await amadeus_client.get(
                "/v1/reference-data/locations/hotels/by-city",
                params={"cityCode": city_code.upper()},
                resource="hotels",
            )
        except NotFoundError:
            return []

        hotels = data.get("data", [])[:limit]
        return [h["hotelId"] for h in hotels if "hotelId" in h]

    @staticmethod
    def _normalize_offers(raw_results: list[dict], check_in: date, check_out: date) -> list[HotelOffer]:
        offers: list[HotelOffer] = []
        for entry in raw_results:
            hotel = entry.get("hotel", {})
            for offer in entry.get("offers", []):
                price = offer.get("price", {})
                room = offer.get("room", {})
                offers.append(
                    HotelOffer(
                        hotel_id=hotel.get("hotelId", ""),
                        hotel_name=hotel.get("name", "Unknown Hotel"),
                        offer_id=offer.get("id", ""),
                        latitude=hotel.get("latitude"),
                        longitude=hotel.get("longitude"),
                        check_in=check_in,
                        check_out=check_out,
                        room_description=(room.get("description") or {}).get("text"),
                        price_total=float(price.get("total", 0)),
                        currency=price.get("currency", "USD"),
                        provider="amadeus",
                    )
                )
        return offers
