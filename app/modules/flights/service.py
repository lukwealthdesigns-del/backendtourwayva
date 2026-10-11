"""
FlightService - "reuse before regenerate" (Blueprint §3, §23) with the shortest cache TTL (3 min): flight prices are the
most volatile data in the platform. Providers come from the resolver in priority order (Travelpayouts, then Amadeus when
configured). Cached price data (Travelpayouts) is labelled CACHED; a live quote is VERIFIED_LIVE.
"""
from __future__ import annotations

from datetime import date
from typing import Optional

from app.core.exceptions import ProviderUnavailableError
from app.modules.flights.schemas import FlightOfferResponse, FlightSearchResponse
from app.providers.travel.registry import get_resolver
from app.providers.travel.resolver import Capability
from app.providers.travel.status import DataStatus, downgrade_for_cache, provenance
from app.services.cache_service import TTL_FLIGHT_SEARCH_SECONDS, CacheService, flight_search_cache_key


def _response(o) -> FlightOfferResponse:
    meta = provenance(o.provider, o.offer_id, DataStatus.VERIFIED_LIVE if o.live_quote else DataStatus.CACHED, ttl_seconds=TTL_FLIGHT_SEARCH_SECONDS)
    return FlightOfferResponse(
        offer_id=o.offer_id, origin=o.origin, destination=o.destination, departure_time=o.departure_time,
        arrival_time=o.arrival_time, duration_iso8601=o.duration_iso8601, stops=o.stops, airline_codes=o.airline_codes,
        cabin=o.cabin, price_total=o.price_total, currency=o.currency, provider=o.provider, booking_link=o.booking_link,
        data_status=meta["data_status"], last_verified_at=meta["last_verified_at"], expires_at=meta["expires_at"],
    )


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
                results=[FlightOfferResponse(**downgrade_for_cache(r)) for r in cached], source="cache", count=len(cached)
            )

        providers = get_resolver().providers(Capability.FLIGHT_SEARCH)
        if not providers:
            raise ProviderUnavailableError("Flight search is not configured.")
        offers, last_error = [], None  # type: list, Optional[Exception]
        for _name, provider in providers:
            try:
                offers = await provider.search_flights(
                    origin=origin, destination=destination, departure_date=departure_date,
                    return_date=return_date, adults=adults, cabin=cabin, max_results=max_results,
                )
            except ProviderUnavailableError as exc:
                last_error = exc
                continue
            if offers:
                break
        if not offers and last_error is not None:
            raise last_error
        results = [_response(o) for o in offers]

        await CacheService.set_json(
            cache_key, [r.model_dump(mode="json") for r in results], TTL_FLIGHT_SEARCH_SECONDS
        )
        return FlightSearchResponse(results=results, source="live", count=len(results))
