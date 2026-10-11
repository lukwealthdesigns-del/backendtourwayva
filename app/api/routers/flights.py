"""Flight search endpoint (Master Blueprint §23)."""
from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, Query

from app.api.dependencies import get_current_user, require_feature, rate_limit
from app.core.constants import FeatureFlag
from app.db.models.user import User
from app.modules.flights.schemas import FlightSearchRequest, FlightSearchResponse
from app.modules.flight_status.schemas import FlightStatusResponse
from app.modules.flight_status.service import FlightStatusService
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


@router.get("/status", response_model=FlightStatusResponse, dependencies=[Depends(rate_limit(bucket="flights:status", max_requests=60, window_seconds=300, per="user")), Depends(require_feature(FeatureFlag.FLIGHTS))])
async def flight_status(
    flight_iata: str = Query(..., min_length=3, max_length=8, description="Flight number such as BA75"),
    flight_date: Optional[str] = Query(default=None, pattern=r"^\d{4}-\d{2}-\d{2}$"),
    current_user: User = Depends(get_current_user),
):
    """Where a flight is and whether it is on time, from the flight-status provider. found=false means the provider has
    no record of it; a status is never guessed."""
    return await FlightStatusService().get(flight_iata=flight_iata, flight_date=flight_date)
