"""Activity search endpoint (Master Blueprint §24)."""
from __future__ import annotations

from fastapi import APIRouter, Depends

from app.api.dependencies import get_current_user, require_feature, rate_limit
from app.core.constants import FeatureFlag
from app.db.models.user import User
from app.modules.activities.schemas import ActivitySearchRequest, ActivitySearchResponse
from app.modules.activities.service import ActivityService

router = APIRouter(prefix="/activities", tags=["Activities"])
_service = ActivityService()


@router.post("/search", response_model=ActivitySearchResponse, dependencies=[Depends(rate_limit(bucket="activities:search", max_requests=30, window_seconds=300, per="user")), Depends(require_feature(FeatureFlag.ACTIVITIES))])
async def search_activities(payload: ActivitySearchRequest, current_user: User = Depends(get_current_user)):
    return await _service.search(
        latitude=payload.latitude, longitude=payload.longitude, radius_km=payload.radius_km
    )
