"""
HotelService — implements "reuse before regenerate" (Blueprint §3,
§20-22) with a deliberately very short TTL (5 min): hotel availability
and prices change quickly, so the cache exists to absorb repeated
identical searches (e.g. a user re-opening the same trip), not to
serve stale prices.
"""
from __future__ import annotations

from datetime import date

from app.modules.hotels.schemas import HotelOfferResponse, HotelSearchResponse
from app.providers.hotels.amadeus_provider import AmadeusHotelProvider
from app.services.cache_service import TTL_HOTEL_SEARCH_SECONDS, CacheService, hotel_search_cache_key

_provider = AmadeusHotelProvider()
_CITY_CODE_CACHE_TTL_SECONDS = 60 * 60 * 24 * 30   # a destination's IATA city code never changes


class HotelService:
    async def search(
        self, *, city_code: str, check_in: date, check_out: date, adults: int, max_hotels: int = 20
    ) -> HotelSearchResponse:
        cache_key = hotel_search_cache_key(city_code, check_in.isoformat(), check_out.isoformat(), adults)

        cached = await CacheService.get_json(cache_key)
        if cached is not None:
            return HotelSearchResponse(
                results=[HotelOfferResponse(**r) for r in cached], source="cache", count=len(cached)
            )

        offers = await _provider.search_hotels(
            city_code=city_code, check_in=check_in, check_out=check_out, adults=adults, max_hotels=max_hotels
        )
        results = [
            HotelOfferResponse(
                hotel_id=o.hotel_id, hotel_name=o.hotel_name, offer_id=o.offer_id,
                latitude=o.latitude, longitude=o.longitude, check_in=o.check_in, check_out=o.check_out,
                room_description=o.room_description, price_total=o.price_total, currency=o.currency,
                provider=o.provider,
            )
            for o in offers
        ]

        await CacheService.set_json(
            cache_key, [r.model_dump(mode="json") for r in results], TTL_HOTEL_SEARCH_SECONDS
        )
        return HotelSearchResponse(results=results, source="live", count=len(results))

    async def resolve_city_code(self, destination: str) -> "str | None":
        """IATA city code for a free-text destination, cached long-term (these are stable)."""
        normalized = destination.strip().lower()
        if not normalized:
            return None
        cache_key = f"hotels:city-code:{normalized}"
        cached = await CacheService.get_raw(cache_key)
        if cached is not None:
            return cached or None
        code = await _provider.resolve_city_code(destination)
        await CacheService.set_raw(cache_key, code or "", _CITY_CODE_CACHE_TTL_SECONDS)
        return code

    async def get_offer(
        self, *, hotel_id: str, check_in: date, check_out: date, adults: int
    ) -> "HotelOfferResponse | None":
        """A single hotel's current live offer (for replacement/re-verification) — no
        caching: a hotel swap must use a fresh price, not a 5-minute-old cached one."""
        offers = await _provider.search_hotels(
            city_code="", check_in=check_in, check_out=check_out, adults=adults, hotel_ids=[hotel_id]
        )
        if not offers:
            return None
        offer = offers[0]
        return HotelOfferResponse(
            hotel_id=offer.hotel_id, hotel_name=offer.hotel_name, offer_id=offer.offer_id,
            latitude=offer.latitude, longitude=offer.longitude, check_in=offer.check_in, check_out=offer.check_out,
            room_description=offer.room_description, price_total=offer.price_total, currency=offer.currency,
            provider=offer.provider,
        )
