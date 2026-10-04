"""
CurrencyAPI.com provider.

Docs: https://currencyapi.com/docs/

Never fabricates a rate: if not configured or the request fails,
raises ProviderUnavailableError (Master Blueprint §78, §108).
"""
from __future__ import annotations

from datetime import datetime, timezone

import httpx

from app.core.config import settings
from app.core.redaction import redact_secrets
from app.core.resilience import CircuitOpenError
from app.providers.http_resilience import resilient_request, safe_error_text
from app.core.exceptions import NotFoundError, ProviderUnavailableError
from app.core.logging import get_logger
from app.providers.currency.interface import CurrencyProvider, ExchangeRate

logger = get_logger(__name__)

_BASE_URL = "https://api.currencyapi.com/v3/latest"


class CurrencyAPIProvider(CurrencyProvider):
    async def get_latest_rate(self, base: str, target: str) -> ExchangeRate:
        if not settings.CURRENCY_API_KEY:
            raise ProviderUnavailableError("Currency provider is not configured.")

        params = {
            "apikey": settings.CURRENCY_API_KEY,
            "base_currency": base.upper(),
            "currencies": target.upper(),
        }

        async def attempt() -> dict:
            async with httpx.AsyncClient(timeout=8.0) as client:
                resp = await client.get(_BASE_URL, params=params)
                resp.raise_for_status()
                return resp.json()

        try:
            data = await resilient_request("currencyapi", attempt)
        except httpx.HTTPStatusError as exc:
            logger.error("currencyapi_http_error", status=exc.response.status_code, body=safe_error_text(exc))
            raise ProviderUnavailableError("Currency lookup failed. Please try again.") from exc
        except CircuitOpenError:
            raise
        except Exception as exc:  # noqa: BLE001
            logger.error("currencyapi_error", error=redact_secrets(exc))
            raise ProviderUnavailableError("Currency lookup failed. Please try again.") from exc

        rate_data = (data.get("data") or {}).get(target.upper())
        if not rate_data:
            raise NotFoundError(f"No exchange rate found for {base.upper()} -> {target.upper()}.")

        return ExchangeRate(
            base=base.upper(),
            target=target.upper(),
            rate=float(rate_data["value"]),
            fetched_at=datetime.now(timezone.utc),
            provider="currencyapi",
        )
