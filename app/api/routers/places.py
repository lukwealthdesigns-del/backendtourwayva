"""Place reference-data endpoints (Master Blueprint §75)."""
from __future__ import annotations

from typing import Optional

import httpx
from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies import get_current_user, rate_limit, require_admin_permission
from app.core.config import settings
from app.providers.google_maps.parsing import PLACES_BASE, verify_photo
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


@router.get(
    "/photo", include_in_schema=True,
    dependencies=[Depends(rate_limit(bucket="places:photo", max_requests=300, window_seconds=60, per="ip"))],
)
async def place_photo(ref: str = Query(..., max_length=400), exp: int = Query(...), sig: str = Query(..., max_length=64)):
    """A Google Places photo through OUR server, so the browser never sees the Google key. The link is signed and
    expires (see sign_photo), so it cannot be used to spend quota on arbitrary photos."""
    if not settings.GOOGLE_MAPS_API_KEY or not verify_photo(settings.SECRET_KEY, ref, exp, sig):
        raise HTTPException(status_code=404, detail="Not found.")
    url = f"{PLACES_BASE}/{ref}/media"
    try:
        async with httpx.AsyncClient(timeout=10.0, follow_redirects=True) as client:
            upstream = await client.get(url, params={"maxWidthPx": settings.GOOGLE_PLACES_PHOTO_MAX_WIDTH, "key": settings.GOOGLE_MAPS_API_KEY})
        upstream.raise_for_status()
    except httpx.HTTPError:
        raise HTTPException(status_code=404, detail="Not found.")
    media_type = upstream.headers.get("content-type", "")
    if not media_type.startswith("image/"):
        raise HTTPException(status_code=404, detail="Not found.")
    return Response(content=upstream.content, media_type=media_type, headers={"Cache-Control": "public, max-age=86400"})
