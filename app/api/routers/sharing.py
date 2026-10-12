"""
Public trip share links.

Owner (signed in, trip owner only):
  POST   /trips/{trip_id}/share-link      create a link (replaces the previous one); the token is shown ONCE
  GET    /trips/{trip_id}/share-link      is a link active, when does it expire, how many views
  DELETE /trips/{trip_id}/share-link      turn it off now

Public (no sign-in, rate limited by IP, never indexed or cached):
  GET    /public/trips/{token}            the read-only trip (itinerary only: never budget, costs, notes or booking links)
  POST   /public/trips/{token}/report     report it to the moderators
"""
from __future__ import annotations

import uuid
from typing import Optional

from fastapi import APIRouter, Depends, Response, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies import get_client_ip, get_current_user, rate_limit, require_feature
from app.core.constants import FeatureFlag
from app.db.models.user import User
from app.db.session import get_db
from app.modules.sharing.schemas import CreateShareLinkRequest, PublicTrip, ReportRequest, ShareLinkCreated, ShareLinkStatus
from app.modules.sharing.service import SharingService

router = APIRouter(prefix="/trips", tags=["Sharing"])
public_router = APIRouter(prefix="/public/trips", tags=["Public"])


@router.post(
    "/{trip_id}/share-link", response_model=ShareLinkCreated, status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(require_feature(FeatureFlag.COLLABORATION)), Depends(rate_limit(bucket="share:create", max_requests=20, window_seconds=3600, per="user"))],
)
async def create_share_link(trip_id: uuid.UUID, payload: CreateShareLinkRequest, current_user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    """Anyone with the link can read the itinerary (not the budget, costs or notes). Creating a new link turns the old one off."""
    return await SharingService(db).create_link(trip_id=trip_id, user=current_user, expires_in_days=payload.expires_in_days)


@router.get("/{trip_id}/share-link", response_model=ShareLinkStatus)
async def share_link_status(trip_id: uuid.UUID, current_user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    return await SharingService(db).status(trip_id=trip_id, user=current_user)


@router.delete("/{trip_id}/share-link", status_code=status.HTTP_204_NO_CONTENT)
async def revoke_share_link(trip_id: uuid.UUID, current_user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    await SharingService(db).revoke(trip_id=trip_id, user=current_user)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@public_router.get(
    "/{token}", response_model=PublicTrip,
    dependencies=[Depends(rate_limit(bucket="public:trip", max_requests=60, window_seconds=60, per="ip"))],
)
async def read_public_trip(token: str, response: Response, db: AsyncSession = Depends(get_db)):
    response.headers["Cache-Control"] = "no-store"          # a turned-off link must stop working at once
    response.headers["X-Robots-Tag"] = "noindex, nofollow"  # shared trips are for the people holding the link, not for search engines
    return await SharingService(db).public_view(token)


@public_router.post(
    "/{token}/report", status_code=status.HTTP_202_ACCEPTED,
    dependencies=[Depends(rate_limit(bucket="public:trip-report", max_requests=5, window_seconds=3600, per="ip"))],
)
async def report_public_trip(token: str, payload: ReportRequest, client_ip: Optional[str] = Depends(get_client_ip), db: AsyncSession = Depends(get_db)):
    await SharingService(db).report(token=token, reason=payload.reason, details=payload.details, client_ip=client_ip)
    return {"received": True}
