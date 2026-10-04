"""Strict parsing of untrusted model output + prompt construction."""
import json
from datetime import date, time

import pytest

from app.modules.planning.domain import PlannedDay, PlannedItem, PlannedTrip, TripSpec
from app.modules.planning.llm_io import (
    MAX_ITEMS_PER_DAY,
    PlanParseError,
    build_generation_messages,
    build_repair_messages,
    extract_json_object,
    parse_llm_plan,
    plan_to_dict,
    weather_summary,
)

SPEC = TripSpec(destination="Lagos", start_date=date(2026, 11, 1), end_date=date(2026, 11, 2), travelers=3,
                budget_amount=900000.0, budget_currency="NGN")


def test_extract_json_tolerates_fences_and_chatter():
    assert extract_json_object('```json\n{"a": 1}\n```') == {"a": 1}
    assert extract_json_object('Sure! Here you go: {"a": {"b": 2}} Enjoy.') == {"a": {"b": 2}}


@pytest.mark.parametrize("bad", ["", "no json here", "{broken", "[1, 2]", None])
def test_extract_json_rejects_garbage(bad):
    with pytest.raises(PlanParseError):
        extract_json_object(bad)


def test_parse_maps_days_by_number_and_pads_missing_days():
    raw = json.dumps({"overview": " Nice ", "days": [
        {"day_number": 2, "items": [{"title": "Beach", "item_type": "activity"}]},
        {"day_number": 1, "items": [{"title": "Museum", "item_type": "attraction"}]},
    ]})
    plan = parse_llm_plan(raw, SPEC, "NGN")
    assert plan.overview == "Nice"
    assert [d.date for d in plan.days] == [date(2026, 11, 1), date(2026, 11, 2)]
    assert [i.title for i in plan.days[0].items] == ["Museum"]

    short = parse_llm_plan(json.dumps({"days": [{"day_number": 1, "items": [{"title": "Only day"}]}]}), SPEC, "NGN")
    assert len(short.days) == 2 and short.days[1].items == []      # padded, then flagged by validation


def test_parse_clamps_and_sanitizes_every_field():
    raw = json.dumps({"days": [{"day_number": 1, "items": [
        {"title": "x" * 500, "item_type": "spaceship", "start_time": "25:99", "end_time": "9:5",
         "estimated_cost": -10, "outdoor": "yes", "must_see": True, "external_id": "h1"},
        {"title": "Priced", "estimated_cost": "1200.456", "start_time": "09:30", "end_time": "10:15:00"},
        {"title": "   "}, {"description": "no title"}, "not a dict", None,
        {"title": "NaN cost", "estimated_cost": float("nan")},
    ]}]})
    items = parse_llm_plan(raw, SPEC, "NGN").days[0].items
    assert [i.title[:5] for i in items] == ["xxxxx", "Price", "NaN c"]      # untitled / non-dict entries dropped
    weird, priced, nan_item = items
    assert len(weird.title) == 200 and weird.item_type == "custom"
    assert weird.start_time is None and weird.end_time is None              # invalid times ignored, not guessed
    assert weird.estimated_cost is None and weird.currency is None          # negative price rejected
    assert weird.outdoor is False and weird.must_see is True                # only a real `true` counts
    assert priced.estimated_cost == 1200.46 and priced.currency == "NGN"
    assert (priced.start_time, priced.end_time) == (time(9, 30), time(10, 15))
    assert nan_item.estimated_cost is None


def test_parse_limits_items_per_day_and_never_trusts_model_coordinates():
    raw = json.dumps({"days": [{"day_number": 1, "items": [
        {"title": f"i{n}", "latitude": 1.0, "longitude": 2.0} for n in range(40)]}]})
    items = parse_llm_plan(raw, SPEC, "NGN").days[0].items
    assert len(items) == MAX_ITEMS_PER_DAY
    assert all(i.latitude is None and i.longitude is None and i.source == "estimated" for i in items)


@pytest.mark.parametrize("raw", ['{"days": []}', '{"days": "nope"}', '{"overview": "x"}'])
def test_parse_requires_days(raw):
    with pytest.raises(PlanParseError):
        parse_llm_plan(raw, SPEC, "NGN")


def test_generation_prompt_carries_the_rules_and_only_the_verified_data_given():
    messages = build_generation_messages(
        spec=SPEC, currency="NGN", preferences={"pace": "relaxed", "avoid": ["clubs"], "empty": None},
        memories=["likes seafood"], geo={"latitude": 6.5, "longitude": 3.4, "country": "NG"},
        hotels=[{"id": "h1", "name": "H"}], activities=[{"id": "a1", "name": "A"}], forecast=[])
    system, user = messages[0]["content"], json.loads(messages[1]["content"])
    assert "EXACTLY 2 days" in system and "3 traveller(s)" in system and "NGN" in system
    assert "external_id" in system and "whole group" in system
    assert user["preferences"] == {"pace": "relaxed", "avoid": ["clubs"]}    # empty values dropped
    assert user["verified_hotels"] == [{"id": "h1", "name": "H"}] and user["known_about_traveller"] == ["likes seafood"]
    assert user["dates"] == ["2026-11-01", "2026-11-02"]


def test_repair_prompt_lists_the_problems_and_the_current_plan():
    from app.modules.planning.domain import Issue

    plan = PlannedTrip(overview="o", currency="NGN", days=[
        PlannedDay(1, date(2026, 11, 1), [PlannedItem("activity", "A", start_time=time(9), end_time=time(10))])])
    messages = build_repair_messages(spec=SPEC, currency="NGN", plan=plan,
                                     issues=[Issue("over_budget", "error", "too much", extra={})],
                                     hotels=[], activities=[])
    body = json.loads(messages[1]["content"])
    assert body["problems_to_fix"][0]["code"] == "over_budget"
    assert body["current_plan"]["days"][0]["items"][0]["start_time"] == "09:00"
    assert "REVISING" in messages[0]["content"]
    assert plan_to_dict(plan)["currency"] == "NGN"


def test_weather_summary_formats_partial_data():
    assert weather_summary({"condition": "Light rain", "high_c": 19.4, "low_c": 13.6, "chance_of_rain_pct": 60}) \
        == "Light rain, 14-19°C, 60% chance of rain"
    assert weather_summary({"condition": "Sunny"}) == "Sunny"
