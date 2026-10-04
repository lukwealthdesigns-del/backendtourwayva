"""Deterministic opening-hours provider for tests and local development (Blueprint §92)."""
from __future__ import annotations

from typing import Optional

from app.providers.opening_hours.interface import OpeningHoursProvider, OpeningHoursResult
from app.providers.opening_hours.matching import normalize_name


class MockOpeningHoursProvider(OpeningHoursProvider):
    def __init__(self, hours_by_name: Optional[dict[str, str]] = None):
        self._hours = {normalize_name(k): v for k, v in (hours_by_name or {}).items()}
        self.calls: list[str] = []
        self.fail = False

    async def lookup(self, *, name: str, latitude: float, longitude: float) -> Optional[OpeningHoursResult]:
        self.calls.append(name)
        if self.fail:
            return None
        raw = self._hours.get(normalize_name(name))
        return OpeningHoursResult(raw=raw, source="mock", matched_name=name) if raw else None
