"""Geocoding provider abstraction (Master Blueprint §29-30)."""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Optional


@dataclass(frozen=True)
class GeocodeResult:
    formatted_address: str
    latitude: float
    longitude: float
    country: Optional[str]      # ISO 3166-1 alpha-2
    city: Optional[str]
    region: Optional[str]
    provider: str
    confidence: Optional[int] = None


class GeocodingProvider(ABC):
    @abstractmethod
    async def forward_geocode(self, query: str) -> GeocodeResult:
        """Turn a free-text place name/address into coordinates."""
        raise NotImplementedError

    @abstractmethod
    async def reverse_geocode(self, latitude: float, longitude: float) -> GeocodeResult:
        """Turn coordinates into a formatted address/place."""
        raise NotImplementedError

    async def search_places(self, query: str, limit: int = 6) -> list[GeocodeResult]:
        """Up to `limit` candidate places for an autocomplete dropdown. Providers that can return several matches
        override this; the default falls back to the single best match so existing/mock providers keep working."""
        from app.core.exceptions import NotFoundError  # local import: interface stays dependency-free at import time

        try:
            return [await self.forward_geocode(query)]
        except NotFoundError:
            return []
