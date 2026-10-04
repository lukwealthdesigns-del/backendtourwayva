"""
Amadeus flight provider (Master Blueprint §23) — Flight Offers Search
API (GET /v2/shopping/flight-offers).

Never invents flight details: prices, times, and stops all come
verbatim from Amadeus's response, never from the LLM (Blueprint §2,
§79).
"""
from __future__ import annotations

from datetime import date
from typing import Optional

from app.core.exceptions import NotFoundError, ProviderUnavailableError
from app.core.logging import get_logger
from app.core.resilience import CircuitOpenError
from app.providers.amadeus.client import amadeus_client
from app.providers.flights.interface import FlightOffer, FlightProvider
from app.services.cache_service import TTL_FLIGHT_OFFER_RAW_SECONDS, CacheService, flight_offer_raw_cache_key

logger = get_logger(__name__)

_CABIN_MAP = {
    "economy": "ECONOMY",
    "premium_economy": "PREMIUM_ECONOMY",
    "business": "BUSINESS",
    "first": "FIRST",
}


class AmadeusFlightProvider(FlightProvider):
    async def search_flights(
        self,
        *,
        origin: str,
        destination: str,
        departure_date: date,
        return_date: Optional[date],
        adults: int,
        cabin: Optional[str] = None,
        max_results: int = 20,
    ) -> list[FlightOffer]:
        params = {
            "originLocationCode": origin.upper(),
            "destinationLocationCode": destination.upper(),
            "departureDate": departure_date.isoformat(),
            "adults": adults,
            "max": max_results,
            "currencyCode": "USD",
        }
        if return_date:
            params["returnDate"] = return_date.isoformat()
        if cabin and cabin.lower() in _CABIN_MAP:
            params["travelClass"] = _CABIN_MAP[cabin.lower()]

        try:
            data = await amadeus_client.get("/v2/shopping/flight-offers", params=params, resource="flights")
        except NotFoundError:
            return []

        raw_offers = data.get("data", [])
        await self._cache_raw_offers(raw_offers)
        return self._normalize_offers(raw_offers, data.get("dictionaries", {}))

    async def reprice_offer(self, *, offer_id: str) -> Optional[FlightOffer]:
        raw_offer = await CacheService.get_json(flight_offer_raw_cache_key(offer_id))
        if raw_offer is None:
            logger.info("amadeus_flight_reprice_cache_miss", offer_id=offer_id)
            return None

        body = {"data": {"type": "flight-offers-pricing", "flightOffers": [raw_offer]}}
        try:
            response = await amadeus_client.post_json(
                "/v1/shopping/flight-offers/pricing", body, resource="flights"
            )
        except (ProviderUnavailableError, NotFoundError, CircuitOpenError) as exc:
            # Could not confirm — NOT the same as confirming it's still valid.
            # Caller falls back to an explicitly-unverified item.
            logger.warning("amadeus_flight_reprice_unconfirmed", offer_id=offer_id, error=str(exc))
            return None

        priced_offers = response.get("data", {}).get("flightOffers", [])
        if not priced_offers:
            return None
        normalized = self._normalize_offers(priced_offers, response.get("dictionaries", {}))
        return normalized[0] if normalized else None

    @staticmethod
    async def _cache_raw_offers(raw_offers: list[dict]) -> None:
        for raw in raw_offers:
            offer_id = raw.get("id")
            if offer_id:
                await CacheService.set_json(flight_offer_raw_cache_key(offer_id), raw, TTL_FLIGHT_OFFER_RAW_SECONDS)

    @staticmethod
    def _normalize_offers(raw_offers: list[dict], dictionaries: dict) -> list[FlightOffer]:
        offers: list[FlightOffer] = []
        for offer in raw_offers:
            itineraries = offer.get("itineraries", [])
            if not itineraries:
                continue

            first_itinerary = itineraries[0]
            segments = first_itinerary.get("segments", [])
            if not segments:
                continue

            first_segment = segments[0]
            last_segment = segments[-1]
            price = offer.get("price", {})

            airline_codes = sorted({seg.get("carrierCode", "") for seg in segments if seg.get("carrierCode")})
            cabin = None
            traveler_pricing = offer.get("travelerPricings", [])
            if traveler_pricing:
                fare_details = traveler_pricing[0].get("fareDetailsBySegment", [])
                if fare_details:
                    cabin = fare_details[0].get("cabin")

            offers.append(
                FlightOffer(
                    offer_id=offer.get("id", ""),
                    origin=first_segment.get("departure", {}).get("iataCode", ""),
                    destination=last_segment.get("arrival", {}).get("iataCode", ""),
                    departure_time=first_segment.get("departure", {}).get("at", ""),
                    arrival_time=last_segment.get("arrival", {}).get("at", ""),
                    duration_iso8601=first_itinerary.get("duration", ""),
                    stops=max(len(segments) - 1, 0),
                    airline_codes=list(airline_codes),
                    cabin=cabin,
                    price_total=float(price.get("total", 0)),
                    currency=price.get("currency", "USD"),
                    provider="amadeus",
                )
            )
        return offers
