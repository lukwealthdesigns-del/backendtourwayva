"""
ActivityService — implements "reuse before regenerate" (Blueprint §3,
§24) with a medium TTL (6 hours): activity listings (tours,
attractions-with-booking) change far less often than hotel/flight
prices, so a longer cache window is safe here.
"""
from __future__ import annotations

from app.modules.activities.schemas import ActivityResponse, ActivitySearchResponse
from app.providers.activities.amadeus_provider import AmadeusActivityProvider
from app.services.cache_service import TTL_ACTIVITY_SEARCH_SECONDS, CacheService, activity_search_cache_key

_provider = AmadeusActivityProvider()


class ActivityService:
    async def search(self, *, latitude: float, longitude: float, radius_km: int = 5) -> ActivitySearchResponse:
        cache_key = activity_search_cache_key(latitude, longitude, radius_km)

        cached = await CacheService.get_json(cache_key)
        if cached is not None:
            return ActivitySearchResponse(
                results=[ActivityResponse(**r) for r in cached], source="cache", count=len(cached)
            )

        activities = await _provider.search_activities(latitude=latitude, longitude=longitude, radius_km=radius_km)
        results = [
            ActivityResponse(
                activity_id=a.activity_id, name=a.name, description=a.description,
                latitude=a.latitude, longitude=a.longitude, price_amount=a.price_amount,
                currency=a.currency, picture_url=a.picture_url, booking_link=a.booking_link,
                provider=a.provider,
            )
            for a in activities
        ]

        await CacheService.set_json(
            cache_key, [r.model_dump(mode="json") for r in results], TTL_ACTIVITY_SEARCH_SECONDS
        )
        return ActivitySearchResponse(results=results, source="live", count=len(results))
