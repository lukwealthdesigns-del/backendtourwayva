"""Place reference-data endpoints (Master Blueprint §75)."""
from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies import get_current_user, require_admin_permission
from app.core.constants import PlaceCategory
from app.db.models.user import User
from app.db.session import get_db
from app.modules.places.schemas import PlaceCreateRequest, PlaceResponse, PlaceSearchResponse
from app.modules.places.service import PlaceService

router = APIRouter(prefix="/places", tags=["Places"])


@router.post(
    "", response_model=PlaceResponse, status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(require_admin_permission("content:manage"))],
)
async def create_place(
    payload: PlaceCreateRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Requires the 'content:manage' admin permission (CONTENT_ADMIN
    or ADMIN/SUPER_ADMIN) — closed as of Phase 8; this was
    temporarily open to any authenticated user through Phases 2-7."""
    place = await PlaceService(db).create(payload)
    return PlaceResponse.model_validate(place)


@router.get("/search", response_model=PlaceSearchResponse)
async def search_places(
    query: Optional[str] = Query(default=None, min_length=1, max_length=255),
    category: Optional[PlaceCategory] = Query(default=None),
    latitude: Optional[float] = Query(default=None, ge=-90, le=90),
    longitude: Optional[float] = Query(default=None, ge=-180, le=180),
    radius_km: Optional[float] = Query(default=None, gt=0, le=100),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    return await PlaceService(db).search(
        query=query, category=category, latitude=latitude, longitude=longitude, radius_km=radius_km
    )
