"""Destination guides for any place (curated destinations have static guides in the app; everything else uses this)."""
from __future__ import annotations

from fastapi import APIRouter, Depends

from app.api.dependencies import get_current_user, rate_limit, require_feature
from app.core.constants import FeatureFlag
from app.db.models.user import User
from app.modules.destinations.schemas import DestinationGuide, DestinationGuideRequest
from app.modules.destinations.service import DestinationService

router = APIRouter(prefix="/destinations", tags=["Destinations"])


@router.post(
    "/guide", response_model=DestinationGuide,
    dependencies=[Depends(require_feature(FeatureFlag.DISCOVER)),
                  Depends(rate_limit(bucket="destinations:guide", max_requests=20, window_seconds=3600, per="user"))],
)
async def destination_guide(payload: DestinationGuideRequest, current_user: User = Depends(get_current_user)):
    """A short AI-written guide for any city, region or country: overview, best time, suggested stay, a rough daily
    budget (USD), highlights, languages, currency and tips. The place is verified with the geocoder first (404 if it
    does not exist), the output is validated, and results are cached for 30 days. Content is general guidance and
    estimates, labelled as such, not live prices."""
    return await DestinationService().guide(name=payload.name, country=payload.country)
