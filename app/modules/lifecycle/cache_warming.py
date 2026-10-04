"""Keep exchange rates warm (Master Prompt §69 "cache refresh"): the rate cache has a short TTL, so
the first request after expiry pays the provider latency (and may hit a provider outage). A
scheduled pass refreshes the common pairs ahead of demand."""
from __future__ import annotations

from typing import Iterable

from app.core.config import settings
from app.core.logging import get_logger
from app.modules.currency.service import CurrencyService

logger = get_logger(__name__)


def parse_pairs(raw: Iterable[str]) -> list[tuple[str, str]]:
    pairs: list[tuple[str, str]] = []
    for item in raw:
        base, _, target = str(item).partition(":")
        base, target = base.strip().upper(), target.strip().upper()
        if len(base) == 3 and len(target) == 3 and base != target:
            pairs.append((base, target))
    return pairs


async def warm_currency_rates(service: CurrencyService | None = None) -> dict[str, int]:
    service = service or CurrencyService()
    counts = {"warmed": 0, "failed": 0}
    for base, target in parse_pairs(settings.CURRENCY_WARM_PAIRS):
        try:
            await service.get_rate(base, target)
            counts["warmed"] += 1
        except Exception as exc:  # noqa: BLE001 - a provider hiccup on one pair must not stop the rest
            logger.warning("currency_warm_failed", pair=f"{base}:{target}", error=str(exc))
            counts["failed"] += 1
    return counts
