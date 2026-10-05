"""The server-side minimum trip budget: conversion/rounding matches the frontend, only a KNOWN low amount is rejected."""
from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from app.core.config import settings
from app.modules.currency import budget_policy as bp


def _run(coro):
    return asyncio.run(coro)


class Rates:
    def __init__(self, rate=None, boom=False):
        self.rate, self.boom = rate, boom

    async def get_rate(self, base, target):
        if self.boom:
            raise RuntimeError("provider down")
        return SimpleNamespace(rate=self.rate)


def test_nice_ceil_matches_the_frontend_rounding():
    assert bp.nice_ceil(138420) == 140000
    assert bp.nice_ceil(92) == 92
    assert bp.nice_ceil(100) == 100
    assert bp.nice_ceil(0) == 0 and bp.nice_ceil(-5) == 0


def test_minimum_converts_the_dollar_floor():
    assert bp.minimum_for_rate(1500, 100) == 150000
    assert bp.minimum_for_rate(1, 100) == 100


def test_low_budget_in_usd_is_rejected_with_details():
    with pytest.raises(bp.BudgetTooLowError) as e:
        _run(bp.ensure_budget_is_realistic(10, "usd"))
    assert e.value.error_code == "budget_too_low" and e.value.status_code == 422
    assert e.value.details["min_amount"] == 100 and e.value.details["currency"] == "USD"


def test_low_budget_in_another_currency_uses_the_exchange_rate():
    with pytest.raises(bp.BudgetTooLowError) as e:
        _run(bp.ensure_budget_is_realistic(15000, "NGN", rates=Rates(1500)))
    assert e.value.details["min_amount"] == 150000
    _run(bp.ensure_budget_is_realistic(150000, "NGN", rates=Rates(1500)))        # exactly the floor is fine


def test_fails_open_when_rates_are_unavailable_and_when_there_is_nothing_to_check():
    _run(bp.ensure_budget_is_realistic(5, "NGN", rates=Rates(boom=True)))
    _run(bp.ensure_budget_is_realistic(None, "USD"))
    _run(bp.ensure_budget_is_realistic(5, None))


def test_zero_disables_the_check(monkeypatch):
    monkeypatch.setattr(settings, "MIN_TRIP_BUDGET_USD", 0.0)
    _run(bp.ensure_budget_is_realistic(1, "USD"))
