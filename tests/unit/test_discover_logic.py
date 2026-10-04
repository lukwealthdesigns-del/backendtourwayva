"""Pure Discover rules: constraints, cache key, candidate parsing, season fit, weather."""
import json
from datetime import date

import pytest

from app.modules.discover.logic import (
    CandidateParseError,
    best_months,
    build_candidate_messages,
    build_constraints,
    cache_key,
    country_matches,
    extract_json_array,
    parse_candidates,
    same_place,
    season_fit,
    weather_summary,
)
from app.modules.discover.scoring import compute_overall_score

REQUEST = {"budget_amount": 700000, "budget_currency": "ngn", "duration_days": 7, "travelers": 2,
           "interests": [], "max_results": 3}


def constraints(profile=None, **over):
    return build_constraints({**REQUEST, **over}, profile or {})[0]


def test_request_wins_and_profile_only_fills_gaps():
    c, notes = build_constraints({**REQUEST, "interests": ["beaches"]},
                                 {"interests": ["food"], "travel_style": "relaxed", "accommodation_preference": "hotel"})
    assert c["interests"] == ["beaches"]                         # the request is never overridden
    assert c["travel_style"] == "relaxed" and c["accommodation_preference"] == "hotel"
    assert notes == ["Used your saved travel style.", "Used your saved accommodation preference."]
    assert c["budget_currency"] == "NGN"


def test_nothing_is_assumed_without_a_profile():
    c, notes = build_constraints(REQUEST, {})
    assert notes == [] and c["interests"] == [] and c.get("travel_style") is None


def test_cache_key_is_deterministic_and_sensitive_to_what_changes_the_answer():
    base = cache_key(constraints())
    assert cache_key(constraints()) == base
    assert cache_key(constraints(interests=["Food", "food "])) == cache_key(constraints(interests=["food"]))   # normalized
    for change in ({"start_date": "2026-11-01"}, {"budget_amount": 900000}, {"travelers": 3},
                   {"duration_days": 10}, {"interests": ["hiking"]}, {"origin": "Lagos"}):
        assert cache_key(constraints(**change)) != base, change


def test_cache_key_never_contains_user_specific_data():
    a = cache_key(constraints({"visited": ["Paris"], "memories": ["afraid of flying"]}))
    b = cache_key(constraints({"visited": [], "memories": []}))
    assert a == b


def test_candidate_prompt_states_the_budget_is_for_the_whole_group_and_excludes_flights():
    messages = build_candidate_messages(constraints(), count=5, memories=["likes quiet places"], usd_per_unit=1 / 1500)
    system, facts = messages[0]["content"], json.loads(messages[1]["content"])
    assert "TOTAL for the whole group of 2" in system and "NOT international flights" in system
    assert facts["total_budget"] == "700000 NGN"
    assert facts["budget_per_person_per_day"] == "50000.00 NGN"          # 700000 / 2 / 7
    assert facts["budget_per_person_per_day_usd"] == 33.33
    assert facts["known_about_traveller"] == ["likes quiet places"]


GOOD = {"destination": "Lisbon", "country_code": "pt", "reasons": "Great food.", "estimated_daily_cost_usd": 90,
        "best_travel_period": "April to June"}


def test_parse_keeps_only_realistic_sanitized_candidates():
    raw = "```json\n" + json.dumps([
        GOOD,
        {"destination": "Free Town", "estimated_daily_cost_usd": 0},            # "free" would win the budget ranking
        {"destination": "Nan City", "estimated_daily_cost_usd": "lots"},
        {"destination": "Huge City", "estimated_daily_cost_usd": 999999},
        {"destination": "lisbon", "estimated_daily_cost_usd": 80},              # duplicate
        {"estimated_daily_cost_usd": 50}, "junk", None,
        {"destination": "X" * 300, "country_code": "zzz", "estimated_daily_cost_usd": 40},
    ]) + "\n```"
    parsed = parse_candidates(raw, limit=5)
    assert [c["destination"][:6] for c in parsed] == ["Lisbon", "XXXXXX"]
    assert parsed[0]["country_code"] == "PT" and parsed[0]["estimated_daily_cost_usd"] == 90.0
    assert len(parsed[1]["destination"]) == 100 and parsed[1]["country_code"] is None


def test_parse_respects_the_limit_and_rejects_unusable_output():
    many = json.dumps([{**GOOD, "destination": f"City {n}"} for n in range(9)])
    assert len(parse_candidates(many, limit=4)) == 4
    for bad in ("", "no array", "[broken", '{"a": 1}', '[{"destination": "X", "estimated_daily_cost_usd": 0}]'):
        with pytest.raises(CandidateParseError):
            parse_candidates(bad, 3)


def test_extract_json_array_tolerates_chatter():
    assert extract_json_array('Here you go: [1, 2] enjoy') == [1, 2]


def test_country_and_visited_matching():
    assert country_matches("GY", "MY") is False and country_matches("PT", "pt") is True
    assert country_matches(None, "MY") is True and country_matches("PT", None) is True     # unknown => cannot judge
    assert same_place("Paris", "Paris, France") and not same_place("Paris", "Lyon") and not same_place("", "x")


@pytest.mark.parametrize("text,expected", [
    ("April to June", {4, 5, 6}), ("Nov-Mar", {11, 12, 1, 2, 3}), ("May and September", {5, 9}),
    ("December through February", {12, 1, 2}), ("year-round", None), ("", None), (None, None),
])
def test_best_months(text, expected):
    assert best_months(text) == expected


def test_season_fit_measures_overlap_and_is_unknown_without_data():
    assert season_fit(date(2026, 5, 1), date(2026, 5, 8), "April to June") == 1.0
    assert season_fit(date(2026, 8, 1), date(2026, 8, 8), "April to June") == 0.0
    assert season_fit(date(2026, 6, 28), date(2026, 7, 4), "April to June") == 0.5
    assert season_fit(None, None, "April to June") is None and season_fit(date(2026, 5, 1), None, "year-round") is None


def test_season_changes_the_overall_score_only_when_dates_exist():
    assert compute_overall_score(budget_fit=1.0, interest_match=0.5) == 0.8                    # legacy weights
    assert compute_overall_score(budget_fit=1.0, interest_match=0.5, season_fit=1.0) == 0.85
    assert compute_overall_score(budget_fit=1.0, interest_match=0.5, season_fit=0.0) == 0.65


def test_weather_only_describes_days_inside_the_trip():
    forecast = [{"date": "2026-09-26", "high_c": 30, "low_c": 22}, {"date": "2026-09-27", "high_c": 28, "low_c": 20},
                {"date": "2026-09-28", "high_c": 18, "low_c": 12}]
    assert weather_summary(forecast, date(2026, 9, 27), date(2026, 9, 28)) == "Forecast 12-28°C for 2 of your trip days"
    assert weather_summary(forecast, None, None) is None                                       # no dates: no honest claim
    assert weather_summary(forecast, date(2026, 12, 1), date(2026, 12, 5)) is None            # beyond the forecast
