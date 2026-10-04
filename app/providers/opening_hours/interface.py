"""Opening-hours provider abstraction (Master Prompt §4 provider abstraction, §16, §26).

Amadeus Tours & Activities returns no structured opening hours, so hours come
from a separate source behind this interface — swapping OpenStreetMap for a
commercial places API later touches only a new implementation.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Optional


@dataclass(frozen=True)
class OpeningHoursResult:
    raw: str                        # the source's opening-hours string, unparsed (OSM `opening_hours` syntax)
    source: str                     # e.g. "openstreetmap" — always shown to the user with the warning
    matched_name: Optional[str] = None


class OpeningHoursProvider(ABC):
    @abstractmethod
    async def lookup(self, *, name: str, latitude: float, longitude: float) -> Optional[OpeningHoursResult]:
        """Opening hours for the venue called `name` at these coordinates.

        Returns None whenever nothing trustworthy is found — no matching venue,
        no hours recorded, provider down, timeout. It NEVER raises and never
        returns a guess: None means "unknown", which callers must not treat as
        "closed" (Blueprint §108)."""
        raise NotImplementedError
