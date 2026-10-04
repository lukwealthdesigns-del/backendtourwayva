"""Flight search endpoint (Master Blueprint §23)."""
from __future__ import annotations

from fastapi import APIRouter, Depends

from app.api.dependencies import get_current_user, require_feature, rate_limit
from app.core.constants import FeatureFlag
from app.db.models.user import User
from app.modules.flights.schemas import FlightSearchRequest, FlightSearchResponse
from app.modules.flights.service import FlightService

router = APIRouter(prefix="/flights", tags=["Flights"])
_service = FlightService()


@router.post("/search", response_model=FlightSearchResponse, dependencies=[Depends(rate_limit(bucket="flights:search", max_requests=30, window_seconds=300, per="user")), Depends(require_feature(FeatureFlag.FLIGHTS))])
async def search_flights(payload: FlightSearchRequest, current_user: User = Depends(get_current_user)):
    return await _service.search(
        origin=payload.origin,
        destination=payload.destination,
        departure_date=payload.departure_date,
        return_date=payload.return_date,
        adults=payload.adults,
        cabin=payload.cabin,
        max_results=payload.max_results,
    )
