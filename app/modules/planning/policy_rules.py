"""Plan limits for itinerary generation, as pure rules (stdlib only, unit-testable).

Three tiers, picked per user:
  * growth  - the admin's "growth mode" switch is ON: every user gets the generous limits (launch period).
  * premium - growth is OFF and the user has premium AI access (paid plan or an active trial that includes it).
  * free    - everyone else.

When the admin switches growth off, nothing else changes in the code: users simply resolve to premium/free limits.
"""
from __future__ import annotations

from dataclasses import dataclass

# Hard ceilings no admin setting can exceed. A single model reply must still fit a whole "full detail" trip, and a
# year is the longest trip we plan at all.
HARD_MAX_DAYS = 365
HARD_FULL_DETAIL_MAX_DAYS = 14
MIN_CHUNK_DAYS = 3
MAX_CHUNK_DAYS = 10


@dataclass(frozen=True)
class PolicyConfig:
    growth_mode: bool = True
    growth_max_days: int = 120
    premium_max_days: int = 180
    free_max_days: int = 14
    full_detail_max_days: int = 14        # trips up to this length are planned in detail in one go
    chunk_days: int = 7                   # longer trips: detailed plans are built this many days at a time
    # Monthly generation allowance per tier; 0 means unlimited.
    growth_monthly_generations: int = 30
    premium_monthly_generations: int = 30
    free_monthly_generations: int = 5
    cost_alert_usd: float = 0.50          # log an alert when one trip's AI spend passes this


@dataclass(frozen=True)
class EffectiveLimits:
    tier: str                             # "growth" | "premium" | "free"
    growth: bool
    max_days: int
    full_detail_max_days: int
    chunk_days: int
    monthly_limit: int


def clamp(value: int, low: int, high: int) -> int:
    return max(low, min(high, int(value)))


def effective_limits(config: PolicyConfig, *, is_premium: bool) -> EffectiveLimits:
    """The limits that apply to one user right now."""
    full = clamp(config.full_detail_max_days, 1, HARD_FULL_DETAIL_MAX_DAYS)
    chunk = clamp(config.chunk_days, MIN_CHUNK_DAYS, MAX_CHUNK_DAYS)
    if config.growth_mode:
        tier, max_days, monthly = "growth", config.growth_max_days, config.growth_monthly_generations
    elif is_premium:
        tier, max_days, monthly = "premium", config.premium_max_days, config.premium_monthly_generations
    else:
        tier, max_days, monthly = "free", config.free_max_days, config.free_monthly_generations
    return EffectiveLimits(
        tier=tier, growth=config.growth_mode, max_days=clamp(max(max_days, full), full, HARD_MAX_DAYS),
        full_detail_max_days=full, chunk_days=chunk, monthly_limit=max(0, int(monthly)),
    )
