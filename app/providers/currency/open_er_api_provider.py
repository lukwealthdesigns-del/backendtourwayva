"""
ExchangeRate-API "Open Access" endpoint (keyless): https://open.er-api.com/v6/latest/{BASE}

An OPT-IN fallback for when CurrencyAPI has no valid key (CURRENCY_FALLBACK_PROVIDER=open_er_api). Facts from the provider's docs:
no API key; rates update once a day; caching is allowed; the endpoint is rate limited (a 429 means wait about 20 minutes); and ATTRIBUTION is
required wherever the rates are shown: "Rates By Exchange Rate API" linking to https://www.exchangerate-api.com. Its terms are narrower than a
keyed plan for a public commercial product, so for production create a free keyed plan instead and set CURRENCY_API_KEY. Never invents a rate.
"""
from __future__ import annotations

from datetime import datetime, timezone

import httpx

from app.core.exceptions import NotFoundError, ProviderUnavailableError
from app.core.logging import get_logger
from app.core.redaction import redact_secrets
from app.core.resilience import CircuitOpenError
from app.providers.currency.interface import CurrencyProvider, ExchangeRate
from app.providers.http_resilience import resilient_request, safe_error_text

logger = get_logger(__name__)
_URL = "https://open.er-api.com/v6/latest/{base}"
PROVIDER = "exchangerate-api-open"


class OpenErApiProvider(CurrencyProvider):
    async def get_latest_rate(self, base: str, target: str) -> ExchangeRate:
        base, target = base.upper(), target.upper()

        async def attempt() -> dict:
            async with httpx.AsyncClient(timeout=8.0) as client:
                resp = await client.get(_URL.format(base=base))
                resp.raise_for_status()
                return resp.json()

        try:
            data = await resilient_request("exchangerate_open", attempt)
        except httpx.HTTPStatusError as exc:
            logger.error("open_er_api_http_error", status=exc.response.status_code, body=safe_error_text(exc))
            raise ProviderUnavailableError("Currency lookup failed. Please try again.") from exc
        except CircuitOpenError:
            raise
        except Exception as exc:  # noqa: BLE001
            logger.error("open_er_api_error", error=redact_secrets(exc))
            raise ProviderUnavailableError("Currency lookup failed. Please try again.") from exc

        if data.get("result") != "success":                       # the documented success flag: never read rates from an error body
            raise ProviderUnavailableError("Currency lookup failed. Please try again.")
        value = (data.get("rates") or {}).get(target)
        try:
            rate = float(value)
        except (TypeError, ValueError):
            raise NotFoundError(f"No exchange rate found for {base} -> {target}.") from None
        if not rate > 0:
            raise NotFoundError(f"No exchange rate found for {base} -> {target}.")
        return ExchangeRate(base=base, target=target, rate=rate, fetched_at=datetime.now(timezone.utc), provider=PROVIDER)
