"""Hotel search endpoint (Master Blueprint §20)."""
from __future__ import annotations

from fastapi import APIRouter, Depends

from app.api.dependencies import get_current_user, require_feature, rate_limit
from app.core.constants import FeatureFlag
from app.db.models.user import User
from app.modules.hotels.schemas import HotelSearchRequest, HotelSearchResponse
from app.modules.hotels.service import HotelService

router = APIRouter(prefix="/hotels", tags=["Hotels"])
_service = HotelService()


@router.post("/search", response_model=HotelSearchResponse, dependencies=[Depends(rate_limit(bucket="hotels:search", max_requests=30, window_seconds=300, per="user")), Depends(require_feature(FeatureFlag.HOTELS))])
async def search_hotels(payload: HotelSearchRequest, current_user: User = Depends(get_current_user)):
    return await _service.search(
        city_code=payload.city_code,
        check_in=payload.check_in,
        check_out=payload.check_out,
        adults=payload.adults,
        max_hotels=payload.max_hotels,
    )
