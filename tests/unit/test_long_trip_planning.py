"""Long-trip planning: plan limits, the route outline, and chunked generation helpers (all pure, no database)."""
from __future__ import annotations

from datetime import date

import pytest

from app.modules.planning import chunking, outline as ol
from app.modules.planning.domain import PlannedDay, PlannedItem, PlannedTrip, TripSpec
from app.modules.planning.policy_rules import (
    HARD_FULL_DETAIL_MAX_DAYS, HARD_MAX_DAYS, PolicyConfig, effective_limits,
)

SPEC = TripSpec("Dubai", date(2026, 11, 1), date(2026, 12, 15), 2, "Lagos", 4500.0, "USD")   # 45 days


def _outline():
    return ol.new_outline(SPEC, overview="Desert and coast", generated_by="ai", segments=[
        {"start_day": 1, "end_day": 20, "area": "Dubai, UAE", "theme": "City", "highlights": ["Burj"], "budget_share": 0.6},
        {"start_day": 21, "end_day": 45, "area": "Abu Dhabi, UAE", "theme": "", "highlights": [], "budget_share": 0.4},
    ])


# --- policy ---------------------------------------------------------------------------------------------------------
def test_growth_mode_gives_everyone_the_growth_limits():
    for premium in (True, False):
        lim = effective_limits(PolicyConfig(growth_mode=True), is_premium=premium)
        assert (lim.tier, lim.growth, lim.max_days) == ("growth", True, 120)


def test_switching_growth_off_applies_plan_limits():
    cfg = PolicyConfig(growth_mode=False)
    assert effective_limits(cfg, is_premium=True).tier == "premium"
    assert effective_limits(cfg, is_premium=True).max_days == 180
    free = effective_limits(cfg, is_premium=False)
    assert (free.tier, free.max_days, free.monthly_limit) == ("free", 14, 5)


def test_limits_are_clamped_to_hard_ceilings():
    lim = effective_limits(PolicyConfig(growth_max_days=9999, full_detail_max_days=99, chunk_days=99), is_premium=False)
    assert lim.max_days == HARD_MAX_DAYS and lim.full_detail_max_days == HARD_FULL_DETAIL_MAX_DAYS and lim.chunk_days == 10


def test_max_days_never_below_full_detail():
    assert effective_limits(PolicyConfig(growth_mode=False, free_max_days=3), is_premium=False).max_days == 14


# --- outline parsing ---------------------------------------------------------------------------------------------------
def test_parse_outline_repairs_gaps_and_normalizes_budget():
    raw = ('{"overview":"x","segments":[{"start_day":1,"end_day":20,"area":"Dubai, UAE","budget_share":0.6},'
           '{"start_day":25,"end_day":40,"area":"Abu Dhabi, UAE","budget_share":0.4}]}')
    out = ol.parse_outline(raw, SPEC)
    assert [(s["start_day"], s["end_day"]) for s in out["segments"]] == [(1, 20), (21, 45)]
    assert round(sum(s["budget_share"] for s in out["segments"]), 3) == 1.0
    assert ol.outline_is_current(out, SPEC)


def test_parse_outline_rejects_garbage_and_fallback_covers_all_days():
    with pytest.raises(ValueError):
        ol.parse_outline('{"segments": "nope"}', SPEC)
    fb = ol.fallback_outline(SPEC)
    assert fb["segments"][0]["start_day"] == 1 and fb["segments"][0]["end_day"] == 45 and fb["generated_by"] == "fallback"


def test_outline_goes_stale_when_dates_change():
    out = _outline()
    assert ol.outline_is_current(out, SPEC)
    assert not ol.outline_is_current(out, TripSpec("Dubai", date(2026, 11, 2), date(2026, 12, 16)))
    assert not ol.outline_is_current(None, SPEC)


def test_cache_key_ignores_dates_and_budget_but_not_length():
    other = TripSpec("dubai", date(2027, 3, 1), date(2027, 4, 14), 2, None, 99.0, "EUR")
    assert ol.outline_cache_key(SPEC, {}, "en") == ol.outline_cache_key(other, {}, "en")
    shorter = TripSpec("Dubai", date(2026, 11, 1), date(2026, 11, 30), 2)
    assert ol.outline_cache_key(SPEC, {}, "en") != ol.outline_cache_key(shorter, {}, "en")


def test_cached_outline_round_trips():
    cached = ol.to_cache_value(_outline())
    back = ol.from_cache_value(cached, SPEC)
    assert back and back["generated_by"] == "cache" and len(back["segments"]) == 2
    assert ol.from_cache_value({"segments": []}, SPEC) is None


# --- chunks ----------------------------------------------------------------------------------------------------------
def test_chunk_bounds_and_resolution():
    assert chunking.chunk_bounds(45, 7)[0] == (1, 7) and chunking.chunk_bounds(45, 7)[-1] == (43, 45)
    assert chunking.resolve_chunk(45, 7, None, []) == (1, 7)
    assert chunking.resolve_chunk(45, 7, None, [1, 8]) == (15, 21)
    assert chunking.resolve_chunk(45, 7, 8, [1, 8]) == (8, 14)          # re-planning a part is allowed
    with pytest.raises(chunking.ChunkError):
        chunking.resolve_chunk(45, 7, 3, [])
    with pytest.raises(chunking.ChunkError):
        chunking.resolve_chunk(14, 7, None, [1, 8])                      # everything planned


def test_chunk_spec_uses_segment_area_dates_and_budget_share():
    part = chunking.chunk_spec(SPEC, _outline(), 15, 21)
    assert part.destination == "Dubai, UAE"
    assert (part.start_date, part.end_date) == (date(2026, 11, 15), date(2026, 11, 21))
    assert part.origin is None and part.budget_amount == 882.0         # 6 Dubai days (0.6/20 each) + 1 Abu Dhabi day (0.4/25)
    first = chunking.chunk_spec(SPEC, _outline(), 1, 7)
    assert first.origin == "Lagos" and first.budget_amount == 945.0


def test_context_marks_continuing_stay_and_area_changes():
    out = _outline()
    assert chunking.build_trip_context(out, start_day=8, end_day=14, total_days=45)["continuing_stay"] is True
    crossing = chunking.build_trip_context(out, start_day=19, end_day=25, total_days=45)
    assert crossing["also_in_range"] == [{"area": "Abu Dhabi, UAE", "from_day": 21, "to_day": 45}]
    assert any("hotel" in r for r in crossing["rules"])
    assert chunking.build_trip_context(out, start_day=22, end_day=28, total_days=45)["continuing_stay"] is True
    assert chunking.build_trip_context(out, start_day=1, end_day=7, total_days=45)["continuing_stay"] is False


def test_record_chunk_keeps_short_summaries_and_titles():
    plan = PlannedTrip("o", "USD", [PlannedDay(1, date(2026, 11, 8), [
        PlannedItem("hotel", "Hotel X"), PlannedItem("attraction", "Burj Khalifa"), PlannedItem("restaurant", "Al Fahidi Cafe")])])
    out = chunking.record_chunk(_outline(), plan, start_day=8, day_offset=7)
    assert out["chunks_planned"] == [8] and out["summaries"]["8"] == "Burj Khalifa; Al Fahidi Cafe"
    assert out["used_titles"] == ["Burj Khalifa", "Al Fahidi Cafe"]
    again = chunking.record_chunk(out, plan, start_day=8, day_offset=7)
    assert again["chunks_planned"] == [8] and again["used_titles"] == out["used_titles"]
