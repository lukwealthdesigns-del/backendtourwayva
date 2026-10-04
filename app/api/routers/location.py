"""Location endpoints (Master Blueprint §87)."""
from __future__ import annotations

from fastapi import APIRouter, Depends

from app.api.dependencies import get_client_ip, get_current_user, rate_limit
from app.db.models.user import User
from app.modules.geocoding.schemas import (
    ForwardGeocodeRequest,
    GeocodeResponse,
    LocationSuggestRequest,
    LocationSuggestResponse,
    ReverseGeocodeRequest,
)
from app.modules.geocoding.service import GeocodingService
from app.modules.location.schemas import ApproximateLocationResponse
from app.modules.location.service import ApproximateLocationService

router = APIRouter(prefix="/location", tags=["Location"])
_service = GeocodingService()
_locator = ApproximateLocationService()


@router.post("/geocode", response_model=GeocodeResponse, dependencies=[Depends(rate_limit(bucket="location:geocode:geocode", max_requests=60, window_seconds=300, per="user"))])
async def geocode(payload: ForwardGeocodeRequest, current_user: User = Depends(get_current_user)):
    return await _service.forward_geocode(payload.query)


@router.post("/suggest", response_model=LocationSuggestResponse, dependencies=[Depends(rate_limit(bucket="location:suggest", max_requests=240, window_seconds=300, per="user"))])
async def suggest_locations(payload: LocationSuggestRequest, current_user: User = Depends(get_current_user)):
    """Autocomplete for every location picker (header location, Traveling from, trip start and destination): up to
    `limit` cities, regions and countries worldwide matching what the user has typed. Cached; empty list when nothing
    matches. 503 `provider_unavailable` if geocoding is not configured."""
    return await _service.suggest(payload.query, payload.limit)


@router.post("/reverse-geocode", response_model=GeocodeResponse, dependencies=[Depends(rate_limit(bucket="location:geocode:reverse_geocode", max_requests=60, window_seconds=300, per="user"))])
async def reverse_geocode(payload: ReverseGeocodeRequest, current_user: User = Depends(get_current_user)):
    return await _service.reverse_geocode(payload.latitude, payload.longitude)


@router.get("/approximate", response_model=ApproximateLocationResponse, dependencies=[Depends(rate_limit(bucket="location:approximate", max_requests=30, window_seconds=300, per="user"))])
async def approximate_location(
    current_user: User = Depends(get_current_user),
    client_ip: str | None = Depends(get_client_ip),
):
    """IP-based approximate location of the caller (city-level: city, region, country, coordinates,
    timezone), falling back to the country the account was localized to. Approximate by design —
    never GPS-accurate, never stored, and the IP address itself is never returned. A 422
    `location_unavailable` means nothing could be resolved: ask the user to allow device location
    or type a place (then use POST /location/geocode)."""
    return await _locator.resolve(user=current_user, client_ip=client_ip)
