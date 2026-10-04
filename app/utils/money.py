"""Money helpers. Provider APIs (Paystack) take amounts in the currency's
MINOR unit (kobo, pesewas, cents) as integers. Prices are stored as floats
on `plans`, so conversion goes through Decimal with explicit rounding —
never `int(amount * 100)`, which turns 19.99 into 1998 (binary float error).

Only 2-decimal currencies are supported here; the allow-list lives in
settings.PAYSTACK_SUPPORTED_CURRENCIES.
"""
from __future__ import annotations

from decimal import ROUND_HALF_UP, Decimal
from typing import Union

Number = Union[int, float, Decimal, str]

_MINOR_FACTOR = Decimal(100)
_CENT = Decimal("0.01")


def to_minor_units(amount: Number) -> int:
    """19.99 -> 1999, 5000 -> 500000. Rejects zero/negative amounts."""
    value = Decimal(str(amount)).quantize(_CENT, rounding=ROUND_HALF_UP)
    if value <= 0:
        raise ValueError("Amount must be greater than zero.")
    return int(value * _MINOR_FACTOR)


def from_minor_units(minor: int) -> float:
    """1999 -> 19.99 (display only; keep money as integers when computing)."""
    return float((Decimal(minor) / _MINOR_FACTOR).quantize(_CENT))
