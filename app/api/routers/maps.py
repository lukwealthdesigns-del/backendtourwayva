"""Map/routing endpoint (Master Blueprint §30)."""
from __future__ import annotations

from fastapi import APIRouter, Depends

from app.api.dependencies import get_current_user, rate_limit
from app.db.models.user import User
from app.modules.maps.schemas import RouteRequest, RouteResponse
from app.modules.maps.service import MapsService

router = APIRouter(prefix="/maps", tags=["Maps"])
_service = MapsService()


@router.post("/route", response_model=RouteResponse, dependencies=[Depends(rate_limit(bucket="maps:route:get_route", max_requests=60, window_seconds=300, per="user"))])
async def get_route(payload: RouteRequest, current_user: User = Depends(get_current_user)):
    return await _service.get_route(
        origin_lat=payload.origin_lat,
        origin_lon=payload.origin_lon,
        destination_lat=payload.destination_lat,
        destination_lon=payload.destination_lon,
        mode=payload.mode,
    )
