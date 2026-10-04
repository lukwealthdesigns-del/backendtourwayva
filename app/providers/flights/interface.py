"""Flight provider abstraction (Master Blueprint §23)."""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import date
from typing import Optional


@dataclass(frozen=True)
class FlightOffer:
    offer_id: str
    origin: str
    destination: str
    departure_time: str   # ISO 8601 datetime string, as returned by provider
    arrival_time: str
    duration_iso8601: str  # e.g. "PT7H35M"
    stops: int
    airline_codes: list[str]
    cabin: Optional[str]
    price_total: float
    currency: str
    provider: str


class FlightProvider(ABC):
    @abstractmethod
    async def search_flights(
        self,
        *,
        origin: str,
        destination: str,
        departure_date: date,
        return_date: Optional[date],
        adults: int,
        cabin: Optional[str] = None,
        max_results: int = 20,
    ) -> list[FlightOffer]:
        """Search flight offers. `origin`/`destination` are IATA
        airport/city codes. Never invent flight details — an empty
        list means no offers were found, not an error."""
        raise NotImplementedError

    @abstractmethod
    async def reprice_offer(self, *, offer_id: str) -> Optional[FlightOffer]:
        """Re-confirm price and availability for an `offer_id` previously
        returned by `search_flights`, by resubmitting it to the provider
        (flight offers, unlike hotels, cannot be re-fetched by id — only
        re-priced by resending the full offer object the provider issued).

        Returns None — never a guess — when the offer can no longer be
        verified: it fell out of the provider's short-lived cache, or the
        provider rejected/could not confirm it. Callers MUST treat a None
        result as "unverified", not as "still valid" (Blueprint §2: the
        LLM/backend never treats client-supplied data as a live fact)."""
        raise NotImplementedError
