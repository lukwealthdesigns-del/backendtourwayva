"""Hotel search endpoint (Master Blueprint §20)."""
from __future__ import annotations

from fastapi import APIRouter, Depends

from app.api.dependencies import get_current_user_or_guest, get_current_user, require_feature, rate_limit
from app.core.constants import FeatureFlag
from app.db.models.user import User
from app.modules.geocoding.service import GeocodingService
from app.modules.hotels.schemas import HotelDiscoverRequest, HotelSearchRequest, HotelSearchResponse
from app.modules.hotels.service import HotelService

router = APIRouter(prefix="/hotels", tags=["Hotels"])
_service = HotelService()


@router.post("/search", response_model=HotelSearchResponse, dependencies=[Depends(rate_limit(bucket="hotels:search", max_requests=30, window_seconds=300, per="user")), Depends(require_feature(FeatureFlag.HOTELS))])
async def search_hotels(payload: HotelSearchRequest, current_user: User = Depends(get_current_user_or_guest)):
    return await _service.search(
        city_code=payload.city_code,
        check_in=payload.check_in,
        check_out=payload.check_out,
        adults=payload.adults,
        max_hotels=payload.max_hotels,
    )


@router.post("/discover", response_model=HotelSearchResponse, dependencies=[Depends(rate_limit(bucket="hotels:discover", max_requests=30, window_seconds=300, per="user")), Depends(require_feature(FeatureFlag.HOTELS))])
async def discover_hotels(payload: HotelDiscoverRequest, current_user: User = Depends(get_current_user_or_guest)):
    """Which hotels exist around a destination (name, location, rating, photos). NOT rooms or prices: those come from
    /hotels/search when a live inventory provider is configured."""
    lat, lon = payload.latitude, payload.longitude
    if lat is None or lon is None:
        place = await GeocodingService().forward_geocode(payload.destination)
        lat, lon = place.latitude, place.longitude
    return await _service.discover(
        destination=payload.destination, latitude=lat, longitude=lon,
        check_in=payload.check_in, check_out=payload.check_out, max_hotels=payload.max_hotels,
    )
