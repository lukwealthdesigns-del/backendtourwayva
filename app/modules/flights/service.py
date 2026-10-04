"""
FlightService — implements "reuse before regenerate" (Blueprint §3,
§23) with the shortest cache TTL of any Phase 2 provider (3 min):
flight prices are the most volatile data type in the whole platform
(Blueprint §61 explicitly calls out "Flight prices: Very short TTL").
"""
from __future__ import annotations

from datetime import date
from typing import Optional

from app.modules.flights.schemas import FlightOfferResponse, FlightSearchResponse
from app.providers.flights.amadeus_provider import AmadeusFlightProvider
from app.services.cache_service import TTL_FLIGHT_SEARCH_SECONDS, CacheService, flight_search_cache_key

_provider = AmadeusFlightProvider()


class FlightService:
    async def search(
        self,
        *,
        origin: str,
        destination: str,
        departure_date: date,
        return_date: Optional[date],
        adults: int,
        cabin: Optional[str] = None,
        max_results: int = 20,
    ) -> FlightSearchResponse:
        cache_key = flight_search_cache_key(
            origin, destination, departure_date.isoformat(),
            return_date.isoformat() if return_date else None, adults, cabin,
        )

        cached = await CacheService.get_json(cache_key)
        if cached is not None:
            return FlightSearchResponse(
                results=[FlightOfferResponse(**r) for r in cached], source="cache", count=len(cached)
            )

        offers = await _provider.search_flights(
            origin=origin, destination=destination, departure_date=departure_date,
            return_date=return_date, adults=adults, cabin=cabin, max_results=max_results,
        )
        results = [
            FlightOfferResponse(
                offer_id=o.offer_id, origin=o.origin, destination=o.destination,
                departure_time=o.departure_time, arrival_time=o.arrival_time,
                duration_iso8601=o.duration_iso8601, stops=o.stops, airline_codes=o.airline_codes,
                cabin=o.cabin, price_total=o.price_total, currency=o.currency, provider=o.provider,
            )
            for o in offers
        ]

        await CacheService.set_json(
            cache_key, [r.model_dump(mode="json") for r in results], TTL_FLIGHT_SEARCH_SECONDS
        )
        return FlightSearchResponse(results=results, source="live", count=len(results))
