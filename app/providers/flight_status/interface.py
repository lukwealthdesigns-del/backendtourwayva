"""Flight status provider abstraction: where is this flight and is it on time. Never invent a status."""
from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any, Optional


class FlightStatusProvider(ABC):
    @abstractmethod
    async def get_status(self, *, flight_iata: str, flight_date: Optional[str] = None) -> Optional[dict[str, Any]]:
        """The normalized status of a flight (e.g. 'BA75'), or None when the provider has no record of it."""
        raise NotImplementedError
