"""
HotelService - "reuse before regenerate" (Blueprint §3, §20-22) over TWO distinct capabilities:

  * DISCOVERY (Google Places): which hotels exist, where, ratings, photos. No rooms, no prices. Cached for hours.
  * INVENTORY (Amadeus today; Booking.com/Expedia later): live rooms and date-specific prices. Cached for 5 minutes only.

Discovery results are never presented as offers: price_total is None and kind="discovery". When no inventory provider
is configured the service still works; it simply has no prices to offer.
"""
from __future__ import annotations

from datetime import date, datetime, timezone
from typing import Optional

from app.core.exceptions import ProviderUnavailableError
from app.modules.hotels.schemas import HotelOfferResponse, HotelSearchResponse
from app.providers.hotels.interface import HotelOffer
from app.providers.travel.registry import get_resolver
from app.providers.travel.resolver import Capability
from app.providers.travel.status import DataStatus, downgrade_for_cache, provenance
from app.services.cache_service import (
    TTL_ACTIVITY_SEARCH_SECONDS, TTL_HOTEL_SEARCH_SECONDS, CacheService, hotel_search_cache_key,
)

_CITY_CODE_CACHE_TTL_SECONDS = 60 * 60 * 24 * 30   # a destination's IATA city code never changes
_DISCOVERY_PREFIX = "gp:"


def _response(offer: HotelOffer, *, ttl: int) -> HotelOfferResponse:
    status = DataStatus.VERIFIED_LIVE
    return HotelOfferResponse(
        hotel_id=offer.hotel_id, hotel_name=offer.hotel_name, offer_id=offer.offer_id,
        latitude=offer.latitude, longitude=offer.longitude, check_in=offer.check_in, check_out=offer.check_out,
        room_description=offer.room_description, price_total=offer.price_total, currency=offer.currency,
        provider=offer.provider, kind=offer.kind, address=offer.address, rating=offer.rating,
        user_rating_count=offer.user_rating_count, photo_url=offer.photo_url, website=offer.website,
        maps_url=offer.maps_url, price_level=offer.price_level,
        **{**provenance(offer.provider, offer.provider_reference or offer.hotel_id, status, ttl_seconds=ttl, source_url=offer.maps_url), "provider": offer.provider},
    )


def _from_cache(rows: list[dict]) -> list[HotelOfferResponse]:
    return [HotelOfferResponse(**downgrade_for_cache(r)) for r in rows]


class HotelService:
    # ---- discovery (no prices) ----
    def discovery_available(self) -> bool:
        return get_resolver().available(Capability.HOTEL_DISCOVERY)

    async def discover(
        self, *, destination: str, latitude: float, longitude: float, check_in: Optional[date] = None,
        check_out: Optional[date] = None, max_hotels: int = 20,
    ) -> HotelSearchResponse:
        providers = get_resolver().providers(Capability.HOTEL_DISCOVERY)
        if not providers:
            raise ProviderUnavailableError("Hotel discovery is not configured.")
        key = f"hotels:discover:{round(latitude, 2)}:{round(longitude, 2)}:{max_hotels}"
        cached = await CacheService.get_json(key)
        if cached is not None:
            rows = _from_cache(cached)
            return HotelSearchResponse(results=rows, source="cache", count=len(rows))

        last_error: Optional[Exception] = None
        for _name, provider in providers:
            try:
                offers = await provider.discover_hotels(
                    latitude=latitude, longitude=longitude, destination=destination,
                    check_in=check_in, check_out=check_out, max_hotels=max_hotels,
                )
            except ProviderUnavailableError as exc:
                last_error = exc
                continue
            results = [_response(o, ttl=TTL_ACTIVITY_SEARCH_SECONDS) for o in offers]
            await CacheService.set_json(key, [r.model_dump(mode="json") for r in results], TTL_ACTIVITY_SEARCH_SECONDS)
            return HotelSearchResponse(results=results, source="live", count=len(results))
        raise last_error or ProviderUnavailableError("Hotel discovery is unavailable.")

    # ---- inventory (live rooms and prices) ----
    def inventory_available(self) -> bool:
        return get_resolver().available(Capability.HOTEL_INVENTORY)

    async def search(
        self, *, city_code: str, check_in: date, check_out: date, adults: int, max_hotels: int = 20
    ) -> HotelSearchResponse:
        provider = get_resolver().first(Capability.HOTEL_INVENTORY)
        if provider is None:
            raise ProviderUnavailableError("Live hotel availability is not configured.")
        cache_key = hotel_search_cache_key(city_code, check_in.isoformat(), check_out.isoformat(), adults)
        cached = await CacheService.get_json(cache_key)
        if cached is not None:
            rows = _from_cache(cached)
            return HotelSearchResponse(results=rows, source="cache", count=len(rows))

        offers = await provider.search_hotels(
            city_code=city_code, check_in=check_in, check_out=check_out, adults=adults, max_hotels=max_hotels
        )
        results = [_response(o, ttl=TTL_HOTEL_SEARCH_SECONDS) for o in offers]
        await CacheService.set_json(cache_key, [r.model_dump(mode="json") for r in results], TTL_HOTEL_SEARCH_SECONDS)
        return HotelSearchResponse(results=results, source="live", count=len(results))

    async def resolve_city_code(self, destination: str) -> "str | None":
        """IATA city code for a free-text destination, cached long-term. None when no inventory provider is configured."""
        normalized = destination.strip().lower()
        provider = get_resolver().first(Capability.HOTEL_INVENTORY)
        if not normalized or provider is None:
            return None
        cache_key = f"hotels:city-code:{normalized}"
        cached = await CacheService.get_raw(cache_key)
        if cached is not None:
            return cached or None
        code = await provider.resolve_city_code(destination)
        await CacheService.set_raw(cache_key, code or "", _CITY_CODE_CACHE_TTL_SECONDS)
        return code

    async def get_offer(
        self, *, hotel_id: str, check_in: date, check_out: date, adults: int
    ) -> "HotelOfferResponse | None":
        """One hotel for replacement/re-verification, never cached. A discovery id ("gp:...") returns the place WITHOUT a
        price; any other id is re-priced live by the inventory provider."""
        if hotel_id.startswith(_DISCOVERY_PREFIX):
            provider = get_resolver().first(Capability.HOTEL_DISCOVERY)
            if provider is None:
                return None
            hotel = await provider.get_hotel(reference=hotel_id[len(_DISCOVERY_PREFIX):])
            return _response(hotel, ttl=TTL_ACTIVITY_SEARCH_SECONDS) if hotel else None
        provider = get_resolver().first(Capability.HOTEL_INVENTORY)
        if provider is None:
            return None
        offers = await provider.search_hotels(
            city_code="", check_in=check_in, check_out=check_out, adults=adults, hotel_ids=[hotel_id]
        )
        return _response(offers[0], ttl=TTL_HOTEL_SEARCH_SECONDS) if offers else None
