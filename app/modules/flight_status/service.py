"""FlightStatusService: cache-first (2 minutes: a flight's status moves quickly), provider via the resolver."""
from __future__ import annotations

import re
from typing import Optional

from app.core.exceptions import ProviderUnavailableError, ValidationAppError
from app.modules.flight_status.schemas import FlightStatusResponse
from app.providers.travel.registry import get_resolver
from app.providers.travel.resolver import Capability
from app.providers.travel.status import DataStatus, downgrade_for_cache, provenance
from app.services.cache_service import CacheService

_TTL_SECONDS = 120
_IATA_FLIGHT = re.compile(r"^[A-Z0-9]{2}\d{1,4}[A-Z]?$")


class FlightStatusService:
    async def get(self, *, flight_iata: str, flight_date: Optional[str] = None) -> FlightStatusResponse:
        code = (flight_iata or "").replace(" ", "").upper()
        if not _IATA_FLIGHT.match(code):
            raise ValidationAppError("Enter a flight number like BA75.")
        key = f"flight-status:{code}:{flight_date or 'now'}"
        cached = await CacheService.get_json(key)
        if cached is not None:
            return FlightStatusResponse(**{**downgrade_for_cache(cached), "source": "cache"})

        providers = get_resolver().providers(Capability.FLIGHT_STATUS)
        if not providers:
            raise ProviderUnavailableError("Flight status is not configured.")
        last_error: Optional[Exception] = None
        for name, provider in providers:
            try:
                found = await provider.get_status(flight_iata=code, flight_date=flight_date)
            except ProviderUnavailableError as exc:
                last_error = exc
                continue
            if found is None:
                response = FlightStatusResponse(found=False, flight_iata=code, provider=name, source="live")
            else:
                meta = provenance(name, code, DataStatus.VERIFIED_LIVE, ttl_seconds=_TTL_SECONDS)
                response = FlightStatusResponse(found=True, **found, provider=name, data_status=meta["data_status"], last_verified_at=meta["last_verified_at"], source="live")
            await CacheService.set_json(key, response.model_dump(mode="json"), _TTL_SECONDS)
            return response
        raise last_error or ProviderUnavailableError("Flight status is unavailable.")
