"""
ActivityService - "reuse before regenerate" (Blueprint §3, §24), medium TTL (6 hours): attraction listings and
ratings change slowly. Providers come from the resolver in priority order (Google Places first, Amadeus tours when
configured); the first that returns results wins, and an unavailable provider falls through to the next.
"""
from __future__ import annotations

from typing import Optional

from app.core.exceptions import ProviderUnavailableError
from app.modules.activities.schemas import ActivityResponse, ActivitySearchResponse
from app.providers.travel.registry import get_resolver
from app.providers.travel.resolver import Capability
from app.providers.travel.status import DataStatus, downgrade_for_cache, provenance
from app.services.cache_service import TTL_ACTIVITY_SEARCH_SECONDS, CacheService, activity_search_cache_key


def _response(a) -> ActivityResponse:
    meta = provenance(a.provider, a.activity_id, DataStatus.VERIFIED_LIVE, ttl_seconds=TTL_ACTIVITY_SEARCH_SECONDS, source_url=getattr(a, "maps_url", None))
    return ActivityResponse(
        activity_id=a.activity_id, name=a.name, description=a.description, latitude=a.latitude, longitude=a.longitude,
        price_amount=a.price_amount, currency=a.currency, picture_url=a.picture_url, booking_link=a.booking_link,
        provider=a.provider, rating=getattr(a, "rating", None), user_rating_count=getattr(a, "user_rating_count", None),
        address=getattr(a, "address", None), maps_url=getattr(a, "maps_url", None), website=getattr(a, "website", None),
        data_status=meta["data_status"], last_verified_at=meta["last_verified_at"], expires_at=meta["expires_at"],
    )


class ActivityService:
    async def search(self, *, latitude: float, longitude: float, radius_km: int = 5) -> ActivitySearchResponse:
        cache_key = activity_search_cache_key(latitude, longitude, radius_km)

        cached = await CacheService.get_json(cache_key)
        if cached is not None:
            return ActivitySearchResponse(
                results=[ActivityResponse(**downgrade_for_cache(r)) for r in cached], source="cache", count=len(cached)
            )

        providers = get_resolver().providers(Capability.ACTIVITY_DISCOVERY)
        if not providers:
            raise ProviderUnavailableError("Activity search is not configured.")
        activities, last_error = [], None  # type: list,OptionalException
        for _name, provider in providers:
            try:
                activities = await provider.search_activities(latitude=latitude, longitude=longitude, radius_km=radius_km)
            except ProviderUnavailableError as exc:
                last_error = exc
                continue
            if activities:
                break
        if not activities and last_error is not None:
            raise last_error
        results = [_response(a) for a in activities]

        await CacheService.set_json(
            cache_key, [r.model_dump(mode="json") for r in results], TTL_ACTIVITY_SEARCH_SECONDS
        )
        return ActivitySearchResponse(results=results, source="live", count=len(results))
