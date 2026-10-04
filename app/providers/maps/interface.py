"""Map/routing provider abstraction (Master Blueprint §30)."""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from enum import Enum


class TravelMode(str, Enum):
    WALKING = "walking"
    DRIVING = "driving"
    CYCLING = "cycling"
    TRANSIT = "transit"  # not all providers support this — see provider docstrings


@dataclass(frozen=True)
class RouteResult:
    distance_meters: float
    duration_seconds: float
    mode: TravelMode
    provider: str


class MapProvider(ABC):
    @abstractmethod
    async def get_route(
        self,
        *,
        origin_lat: float,
        origin_lon: float,
        destination_lat: float,
        destination_lon: float,
        mode: TravelMode,
    ) -> RouteResult:
        """Distance + travel time between two points. Never fabricate
        a route — raise ProviderUnavailableError on failure/misconfig,
        or NotFoundError/ValidationAppError if the mode is unsupported."""
        raise NotImplementedError
