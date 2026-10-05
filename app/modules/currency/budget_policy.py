"""Server-side minimum for trip budgets.

The web app already refuses absurdly low budgets in the form, but a form is not a security or integrity boundary:
the API is. The floor is expressed in US dollars (`MIN_TRIP_BUDGET_USD`, 0 disables it) and converted to the budget's
currency with the same two-significant-digit rounding the app uses, so the number a user is shown matches the number
the API enforces. If the exchange rate cannot be fetched we let the request through (a rates outage must not stop
people planning); only a *known* too-low amount is rejected.
"""
from __future__ import annotations

import math
from typing import Optional

from app.core.config import settings
from app.core.exceptions import ValidationAppError
from app.core.logging import get_logger

logger = get_logger(__name__)


class BudgetTooLowError(ValidationAppError):
    error_code = "budget_too_low"


def nice_ceil(x: float) -> float:
    """Smallest number >= x that keeps two significant digits (138,420 -> 140,000; 92 -> 92). Mirrors the frontend."""
    if not math.isfinite(x) or x <= 0:
        return 0.0
    step = 10 ** max(0, math.floor(math.log10(x)) - 1)
    return float(math.ceil(x / step) * step)


def minimum_for_rate(usd_to_currency_rate: float, floor_usd: Optional[float] = None) -> float:
    floor = settings.MIN_TRIP_BUDGET_USD if floor_usd is None else floor_usd
    return nice_ceil(floor * usd_to_currency_rate)


async def ensure_budget_is_realistic(amount: Optional[float], currency: Optional[str], *, rates=None) -> None:
    """Raises BudgetTooLowError (422, `budget_too_low`, with details.min_amount/currency) if `amount` is below the floor."""
    if amount is None or not currency or settings.MIN_TRIP_BUDGET_USD <= 0:
        return
    currency = currency.upper()
    try:
        if currency == "USD":
            minimum = nice_ceil(settings.MIN_TRIP_BUDGET_USD)
        else:
            if rates is None:
                from app.modules.currency.service import CurrencyService

                rates = CurrencyService()
            minimum = minimum_for_rate((await rates.get_rate("USD", currency)).rate)
    except Exception as exc:  # noqa: BLE001 - fail open: never block planning because rates are down
        logger.warning("budget_floor_check_skipped", currency=currency, error=str(exc))
        return
    if minimum and amount < minimum:
        raise BudgetTooLowError(
            f"That budget is too low for a trip. Please enter at least {minimum:,.0f} {currency}.",
            details={"min_amount": minimum, "currency": currency, "min_usd": settings.MIN_TRIP_BUDGET_USD},
        )
