"""Discover endpoints (Master Blueprint §10-12)."""
from __future__ import annotations

from fastapi import APIRouter, Depends, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies import get_current_user, rate_limit, require_feature
from app.core.constants import FeatureFlag
from app.db.models.user import User
from app.db.session import get_db
from app.modules.discover.schemas import DiscoverSearchRequest, DiscoverSearchResponse
from app.modules.discover.service import DiscoverService

router = APIRouter(prefix="/discover", tags=["Discover"])


@router.post(
    "/search", response_model=DiscoverSearchResponse, status_code=status.HTTP_201_CREATED,
    dependencies=[
        Depends(require_feature(FeatureFlag.DISCOVER)),
        Depends(rate_limit(bucket="discover:search", max_requests=10, window_seconds=300, per="user")),
    ],
)
async def search_destinations(
    payload: DiscoverSearchRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """AI-proposed, geocoding-verified destination recommendations
    (Blueprint §10-12). Every cost figure is an estimate — see
    CostBreakdown.source, always "estimated", never live pricing."""
    return await DiscoverService(db).search(user_id=current_user.id, payload=payload)
