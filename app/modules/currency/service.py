"""
CurrencyService — implements "reuse before regenerate" (Blueprint §3,
§27-28): checks the Redis cache before calling CurrencyAPI, with a
short/medium TTL since exchange rates move.
"""
from __future__ import annotations

from datetime import datetime, timezone

from app.modules.currency.schemas import ConvertResponse, RateResponse
from app.providers.currency.currencyapi_provider import CurrencyAPIProvider
from app.services import durable_cache
from app.services.cache_service import TTL_CURRENCY_SECONDS, CacheService, currency_rate_cache_key

_provider = CurrencyAPIProvider()


class CurrencyService:
    async def get_rate(self, base: str, target: str) -> RateResponse:
        base, target = base.upper(), target.upper()

        if base == target:
            from datetime import datetime, timezone

            return RateResponse(
                base=base, target=target, rate=1.0,
                fetched_at=datetime.now(timezone.utc), source="identity", provider="none",
            )

        cache_key = currency_rate_cache_key(base, target)
        cached = await CacheService.get_json(cache_key)
        if cached is not None:
            return RateResponse(**{**cached, "source": "cache"})

        # Redis missed (cold start, restart, eviction) — try the durable copy before paying
        # for a live provider call; it carries its own expiry so it is never served stale.
        durable = await durable_cache.get_currency_rate(base, target)
        if durable is not None:
            response = RateResponse(**durable, source="cache")
            await CacheService.set_json(cache_key, response.model_dump(mode="json"), TTL_CURRENCY_SECONDS)
            return response

        rate = await _provider.get_latest_rate(base, target)
        response = RateResponse(
            base=rate.base, target=rate.target, rate=rate.rate,
            fetched_at=rate.fetched_at, source="live", provider=rate.provider,
        )
        await CacheService.set_json(cache_key, response.model_dump(mode="json"), TTL_CURRENCY_SECONDS)
        await durable_cache.set_currency_rate(
            base=response.base, target=response.target, rate=response.rate, provider=response.provider,
            fetched_at=response.fetched_at, ttl_seconds=TTL_CURRENCY_SECONDS,
        )
        return response

    async def convert(self, amount: float, base: str, target: str) -> ConvertResponse:
        rate_response = await self.get_rate(base, target)
        converted = round(amount * rate_response.rate, 2)
        return ConvertResponse(
            original_amount=amount,
            original_currency=rate_response.base,
            converted_amount=converted,
            converted_currency=rate_response.target,
            rate=rate_response.rate,
            fetched_at=rate_response.fetched_at,
            source=rate_response.source,
            provider=rate_response.provider,
        )
