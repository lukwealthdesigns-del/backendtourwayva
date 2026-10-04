from app.modules.discover.scoring import (
    build_cost_breakdown,
    compute_budget_fit_score,
    compute_interest_match_score,
    compute_overall_score,
    is_over_budget,
)


def test_cost_breakdown_parts_sum_to_total():
    cb = build_cost_breakdown(1000.0, "usd")
    assert cb["currency"] == "USD"
    assert cb["source"] == "estimated"
    parts_total = cb["accommodation"] + cb["food"] + cb["transport"] + cb["activities"]
    assert abs(parts_total - 1000.0) < 0.01


def test_budget_fit_comfortably_under():
    assert compute_budget_fit_score(500, 1000) == 1.0


def test_budget_fit_at_taper_start():
    assert compute_budget_fit_score(800, 1000) == 1.0


def test_budget_fit_double_budget_is_zero():
    assert compute_budget_fit_score(2000, 1000) == 0.0


def test_budget_fit_way_over_stays_zero():
    assert compute_budget_fit_score(5000, 1000) == 0.0


def test_budget_fit_midpoint_is_between_zero_and_one():
    score = compute_budget_fit_score(1400, 1000)
    assert 0.0 < score < 1.0


def test_budget_fit_zero_budget_never_divides_by_zero():
    assert compute_budget_fit_score(100, 0) == 0.0


def test_interest_match_no_interests_is_neutral():
    assert compute_interest_match_score([], "anything") == 0.5


def test_interest_match_full_match():
    assert compute_interest_match_score(["beach", "hiking"], "Great beach and hiking trails") == 1.0


def test_interest_match_partial():
    assert compute_interest_match_score(["beach", "hiking"], "Great beach only") == 0.5


def test_interest_match_none():
    assert compute_interest_match_score(["skiing"], "tropical beach vibes") == 0.0


def test_overall_score_weights_budget_more_than_interest():
    budget_only = compute_overall_score(budget_fit=1.0, interest_match=0.0)
    interest_only = compute_overall_score(budget_fit=0.0, interest_match=1.0)
    assert budget_only > interest_only
    assert budget_only == 0.6
    assert interest_only == 0.4


def test_is_over_budget():
    assert is_over_budget(1200, 1000) is True
    assert is_over_budget(900, 1000) is False
    assert is_over_budget(1000, 1000) is False
