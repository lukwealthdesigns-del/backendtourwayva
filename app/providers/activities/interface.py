"""Activity provider abstraction (Master Blueprint §24)."""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Optional


@dataclass(frozen=True)
class ActivityResult:
    activity_id: str
    name: str
    description: Optional[str]
    latitude: float
    longitude: float
    price_amount: Optional[float]
    currency: Optional[str]
    picture_url: Optional[str]
    booking_link: Optional[str]
    provider: str


class ActivityProvider(ABC):
    @abstractmethod
    async def search_activities(
        self, *, latitude: float, longitude: float, radius_km: int = 5
    ) -> list[ActivityResult]:
        """Search activities near a point. Never invent an activity —
        an empty list means none were found within the radius."""
        raise NotImplementedError
