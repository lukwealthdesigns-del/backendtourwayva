"""
Travelpayouts flight price data + affiliate deep links.

Docs: https://support.travelpayouts.com/hc/en-us/articles/203956163 (Aviasales Data API, `prices_for_dates`).
The prices are CACHED search data, not live inventory; offers carry live_quote=False and cannot be re-priced
(`reprice_offer` returns None, which callers must treat as "unverified").
"""
from __future__ import annotations

from datetime import date
from typing import Optional

import httpx

from app.core.config import settings
from app.core.exceptions import ProviderUnavailableError
from app.core.logging import get_logger
from app.core.redaction import redact_secrets
from app.core.resilience import CircuitOpenError
from app.providers.flights.interface import FlightOffer, FlightProvider
from app.providers.flights.travelpayouts_parsing import parse_prices
from app.providers.http_resilience import resilient_request, safe_error_text

logger = get_logger(__name__)
_URL = "https://api.travelpayouts.com/aviasales/v3/prices_for_dates"


class TravelpayoutsFlightProvider(FlightProvider):
    async def search_flights(
        self, *, origin: str, destination: str, departure_date: date, return_date: Optional[date], adults: int,
        cabin: Optional[str] = None, max_results: int = 20,
    ) -> list[FlightOffer]:
        if not settings.TRAVELPAYOUTS_TOKEN:
            raise ProviderUnavailableError("Flight search is not configured.")
        params = {
            "origin": origin.upper(), "destination": destination.upper(), "departure_at": departure_date.isoformat(),
            "currency": "usd", "sorting": "price", "limit": max(1, min(max_results, 50)), "page": 1,
            "one_way": "false" if return_date else "true", "unique": "false",
        }
        if return_date:
            params["return_at"] = return_date.isoformat()
        headers = {"X-Access-Token": settings.TRAVELPAYOUTS_TOKEN, "Accept-Encoding": "gzip, deflate"}

        async def attempt() -> dict:
            async with httpx.AsyncClient(timeout=12.0) as client:
                resp = await client.get(_URL, params=params, headers=headers)
                resp.raise_for_status()
                return resp.json()

        try:
            data = await resilient_request("travelpayouts", attempt)
        except httpx.HTTPStatusError as exc:
            logger.error("travelpayouts_http_error", status=exc.response.status_code, body=safe_error_text(exc))
            raise ProviderUnavailableError("Flight search failed. Please try again.") from exc
        except CircuitOpenError:
            raise
        except Exception as exc:  # noqa: BLE001
            logger.error("travelpayouts_error", error=redact_secrets(exc))
            raise ProviderUnavailableError("Flight search failed. Please try again.") from exc

        return [
            FlightOffer(
                offer_id=o["offer_id"], origin=o["origin"], destination=o["destination"], departure_time=o["departure_time"],
                arrival_time=o["arrival_time"], duration_iso8601=o["duration_iso8601"], stops=o["stops"],
                airline_codes=o["airline_codes"], cabin=cabin, price_total=o["price_total"], currency=o["currency"],
                provider="travelpayouts", booking_link=o["booking_link"], live_quote=False,
            )
            for o in parse_prices(data, adults=adults, marker=settings.TRAVELPAYOUTS_MARKER, limit=max_results)
        ]

    async def reprice_offer(self, *, offer_id: str) -> Optional[FlightOffer]:
        return None   # cached price data cannot be confirmed live
