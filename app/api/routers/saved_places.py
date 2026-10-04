"""Saved-places endpoints (Master Blueprint §75)."""
from __future__ import annotations

import uuid
from typing import Optional

from fastapi import APIRouter, Depends, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies import get_current_user
from app.db.models.user import User
from app.db.session import get_db
from app.modules.saved_places.schemas import SavePlaceRequest, SavedPlaceListResponse, SavedPlaceResponse
from app.modules.saved_places.service import SavedPlaceService

router = APIRouter(prefix="/saved-places", tags=["Saved Places"])


@router.post("", response_model=SavedPlaceResponse, status_code=status.HTTP_201_CREATED)
async def save_place(
    payload: SavePlaceRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Ungated (free tier): bookmarking is core, lightweight functionality, like Discover
    itself. Idempotent — saving the same place again returns the existing bookmark."""
    result = await SavedPlaceService(db).save(user_id=current_user.id, payload=payload)
    return SavedPlaceResponse.model_validate(result.place)


@router.get("", response_model=SavedPlaceListResponse)
async def list_saved_places(
    trip_id: Optional[uuid.UUID] = Query(default=None),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    places = await SavedPlaceService(db).list_for_user(current_user.id, trip_id=trip_id)
    results = [SavedPlaceResponse.model_validate(p) for p in places]
    return SavedPlaceListResponse(results=results, count=len(results))


@router.delete("/{place_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_saved_place(
    place_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    await SavedPlaceService(db).delete(user_id=current_user.id, place_id=place_id)
