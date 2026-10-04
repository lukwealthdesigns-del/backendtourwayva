from __future__ import annotations

from typing import Optional

from app.core.config import settings
from app.core.logging import get_logger
from app.providers.opening_hours.interface import OpeningHoursProvider
from app.providers.opening_hours.overpass_provider import OverpassOpeningHoursProvider

logger = get_logger(__name__)


def get_opening_hours_provider() -> Optional[OpeningHoursProvider]:
    """None means the feature is off: planning then simply skips the opening-hours
    check (it never invents hours to fill the gap)."""
    choice = (settings.OPENING_HOURS_PROVIDER or "none").strip().lower()
    if choice == "overpass":
        return OverpassOpeningHoursProvider()
    if choice not in ("none", ""):
        logger.warning("unknown_opening_hours_provider", value=choice)
    return None
