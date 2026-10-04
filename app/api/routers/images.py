"""Image search endpoint — internal utility used by Discover/Planning
to illustrate destinations, attractions, and activities."""
from __future__ import annotations

from fastapi import APIRouter, Depends, Query

from app.api.dependencies import get_current_user, rate_limit
from app.db.models.user import User
from app.modules.images.schemas import ImageResponse
from app.modules.images.service import ImageService

router = APIRouter(prefix="/images", tags=["Images"])
_service = ImageService()


@router.get("/search", response_model=ImageResponse, dependencies=[Depends(rate_limit(bucket="images:search:search_image", max_requests=60, window_seconds=300, per="user"))])
async def search_image(
    entity: str = Query(..., min_length=2, max_length=200),
    locale: str = Query(default="en", min_length=2, max_length=10),
    current_user: User = Depends(get_current_user),
):
    return await _service.get_or_search(entity, locale)
