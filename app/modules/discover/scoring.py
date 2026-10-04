"""
Pure scoring/cost-breakdown functions for Discover (Master Blueprint
§12: destination scoring). Deliberately separated from
DiscoverService so this logic is testable without a database or
network — see tests/unit/test_discover_scoring.py.

Cost breakdown proportions (accommodation 40% / food 25% / transport
20% / activities 15%) are a fixed heuristic split of one AI-estimated
total, NOT independently verified per-category pricing — every
CostBreakdown carries source="estimated" for exactly this reason.
"""
from __future__ import annotations

_ACCOMMODATION_SHARE = 0.40
_FOOD_SHARE = 0.25
_TRANSPORT_SHARE = 0.20
_ACTIVITIES_SHARE = 0.15


def build_cost_breakdown(total_cost: float, currency: str) -> dict:
    return {
        "accommodation": round(total_cost * _ACCOMMODATION_SHARE, 2),
        "food": round(total_cost * _FOOD_SHARE, 2),
        "transport": round(total_cost * _TRANSPORT_SHARE, 2),
        "activities": round(total_cost * _ACTIVITIES_SHARE, 2),
        "total": round(total_cost, 2),
        "currency": currency.upper(),
        "source": "estimated",
    }


def compute_budget_fit_score(estimated_total: float, budget_amount: float) -> float:
    """1.0 for comfortably under budget, tapering to 0.0 as the
    estimate exceeds budget by 2x or more. Never negative."""
    if budget_amount <= 0:
        return 0.0
    ratio = estimated_total / budget_amount
    if ratio <= 0.8:
        return 1.0
    if ratio >= 2.0:
        return 0.0
    # Linear taper from 1.0 at ratio=0.8 down to 0.0 at ratio=2.0.
    return max(0.0, 1.0 - (ratio - 0.8) / 1.2)


def compute_interest_match_score(interests: list[str], reasons_text: str) -> float:
    """Fraction of the user's stated interests that appear (as a
    substring, case-insensitive) in the AI's reasoning text. Returns
    0.5 (neutral) when no interests were given — absence of a
    preference shouldn't penalize a destination."""
    if not interests:
        return 0.5
    reasons_lower = reasons_text.lower()
    matched = sum(1 for interest in interests if interest.lower() in reasons_lower)
    return matched / len(interests)


def compute_overall_score(*, budget_fit: float, interest_match: float, season_fit: "float | None" = None) -> float:
    """Budget fit is weighted most — a destination that blows the budget
    shouldn't rank highly just because it name-drops every interest.

    Without travel dates (season_fit is None): 0.6 budget + 0.4 interests.
    With dates: 0.5 budget + 0.3 interests + 0.2 season (is the trip in the
    destination's best months?)."""
    if season_fit is None:
        return round((0.6 * budget_fit) + (0.4 * interest_match), 4)
    return round((0.5 * budget_fit) + (0.3 * interest_match) + (0.2 * season_fit), 4)


def is_over_budget(estimated_total: float, budget_amount: float) -> bool:
    return estimated_total > budget_amount
