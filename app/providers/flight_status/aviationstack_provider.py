"""
Aviationstack flight status (https://aviationstack.com/documentation).

Plans differ: the free plan is HTTP-only and real-time only, paid plans add HTTPS and a `flight_date` filter. Set
AVIATIONSTACK_BASE_URL to match your plan. A flight the provider does not know returns None, never a made-up status.
"""
from __future__ import annotations

from typing import Any, Optional

import httpx

from app.core.config import settings
from app.core.exceptions import ProviderUnavailableError
from app.core.logging import get_logger
from app.core.redaction import redact_secrets
from app.core.resilience import CircuitOpenError
from app.providers.flight_status.aviationstack_parsing import parse_status
from app.providers.flight_status.interface import FlightStatusProvider
from app.providers.http_resilience import resilient_request

logger = get_logger(__name__)


class AviationstackProvider(FlightStatusProvider):
    async def get_status(self, *, flight_iata: str, flight_date: Optional[str] = None) -> Optional[dict[str, Any]]:
        if not settings.AVIATIONSTACK_API_KEY:
            raise ProviderUnavailableError("Flight status is not configured.")
        params = {"access_key": settings.AVIATIONSTACK_API_KEY, "flight_iata": flight_iata.upper(), "limit": 20}
        if flight_date:
            params["flight_date"] = flight_date

        async def attempt() -> dict:
            async with httpx.AsyncClient(timeout=10.0) as client:
                resp = await client.get(f"{settings.AVIATIONSTACK_BASE_URL.rstrip('/')}/flights", params=params)
                resp.raise_for_status()
                return resp.json()

        try:
            data = await resilient_request("aviationstack", attempt)
        except CircuitOpenError:
            raise
        except Exception as exc:  # noqa: BLE001  (the key is a query parameter: log the redacted error only)
            logger.error("aviationstack_error", error=redact_secrets(exc))
            raise ProviderUnavailableError("Flight status lookup failed. Please try again.") from exc
        if isinstance(data, dict) and data.get("error"):
            logger.warning("aviationstack_api_error", code=str((data.get("error") or {}).get("code")))
            raise ProviderUnavailableError("Flight status is unavailable on the current plan or key.")
        return parse_status(data, flight_iata, flight_date)
