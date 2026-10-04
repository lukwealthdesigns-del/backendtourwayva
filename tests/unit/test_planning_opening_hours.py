"""Opening-hours verification inside itinerary generation (Master Prompt §16, §26; item-5 hardening).

Amadeus returns no structured opening hours, so the workflow asks a separate
opening-hours port (OpenStreetMap in production) for the timed, located
activities/attractions/restaurants it planned. A conflict is only ever a WARNING
that names its source, and "no data" / "cannot parse" / "provider down" all mean
UNKNOWN — never closed, never a failed plan. Run end to end with fake ports: no
network, no database, no LangGraph.
"""
from __future__ import annotations

import asyncio
import json
from datetime import date

from app.modules.planning.domain import GeoPoint, TripSpec
from app.modules.planning.graph import PlanningPorts, build_generation_spec
from app.modules.planning.workflow import run_locally

PARIS = GeoPoint(latitude=48.8566, longitude=2.3522, formatted_address="Paris, France", country="FR", city="Paris")
SPEC = TripSpec(destination="Paris", start_date=date(2026, 10, 1), end_date=date(2026, 10, 3), travelers=2)
TODAY = date(2026, 9, 25)


def _days(first_start="10:00", first_end="11:00"):
    """Three days, one located activity each; 'Walk 0' is the one the tests move around."""
    return [[{"item_type": "activity", "title": f"Walk {n}", "location_name": f"Park {n}, Paris",
              "start_time": first_start if n == 0 else "10:00", "end_time": first_end if n == 0 else "11:00"}]
            for n in range(3)]


def _plan_json(days):
    return json.dumps({"overview": "A trip.", "days": [{"day_number": i + 1, "items": items} for i, items in enumerate(days)]})


class Run:
    def __init__(self, *, replies, hours=None, enabled=True, max_lookups=15, timeout=20.0, hours_error=None, delay=0.0):
        self.replies = list(replies)
        self.hours = hours or {}
        self.enabled, self.max_lookups, self.timeout = enabled, max_lookups, timeout
        self.hours_error, self.delay = hours_error, delay
        self.llm_messages: list = []
        self.hours_calls: list[str] = []
        self.persisted = None

    def ports(self):
        async def geocode(query):
            return PARIS if query == "Paris" else GeoPoint(latitude=48.86, longitude=2.35, formatted_address=query)

        async def no_hotels(*a):
            return []

        async def no_activities(*a):
            return []

        async def forecast(*a):
            return []

        async def convert_rate(base, target):
            return 1.0

        async def llm(messages, temperature, max_tokens):
            self.llm_messages.append(messages)
            return self.replies.pop(0) if len(self.replies) > 1 else self.replies[0]

        async def persist(plan, meta):
            self.persisted = (plan, meta)
            return {"version_number": 1}

        async def opening_hours(name, latitude, longitude):
            self.hours_calls.append(name)
            if self.delay:
                await asyncio.sleep(self.delay)
            if self.hours_error:
                raise self.hours_error
            return self.hours.get(name)

        return PlanningPorts(
            geocode=geocode, search_hotels=no_hotels, search_activities=no_activities, forecast=forecast,
            convert_rate=convert_rate, llm=llm, persist=persist, today=lambda: TODAY,
            opening_hours=opening_hours, opening_hours_enabled=self.enabled,
            max_opening_hours_lookups=self.max_lookups, opening_hours_timeout_seconds=self.timeout,
        )

    def go(self):
        state = {"spec": SPEC, "currency": "EUR", "preferences": {}, "memories": [], "options": {"city_code": "PAR"}}
        return asyncio.run(run_locally(build_generation_spec(self.ports()), state))

    @property
    def warnings(self):
        return self.persisted[1]["warnings"]

    @property
    def sources(self):
        return self.persisted[1]["data_sources"]


def _hours_warnings(run):
    return [w for w in run.warnings if "OpenStreetMap" in w]


def test_a_visit_before_opening_is_reported_as_a_warning_and_never_blocks_the_plan():
    run = Run(replies=[_plan_json(_days())], hours={"Walk 0": "Mo-Su 11:00-18:00"})
    state = run.go()

    assert "error" not in state and run.persisted is not None
    (warning,) = _hours_warnings(run)
    assert "'Walk 0' starts at 10:00 on day 1 (Thursday)" in warning and "11:00–18:00" in warning
    assert "can be out of date" in warning                       # the user is told the source is fallible
    assert run.sources["opening_hours"] == "ok"


def test_the_model_gets_one_chance_to_fix_it_and_is_not_looped_on():
    run = Run(replies=[_plan_json(_days())], hours={"Walk 0": "Mo-Su 11:00-18:00"})
    run.go()

    assert len(run.llm_messages) == 2                             # draft + exactly ONE repair round
    repair_payload = json.loads(run.llm_messages[1][1]["content"])
    problems = repair_payload["problems_to_fix"]
    assert [p["code"] for p in problems] == ["opening_hours_conflict"]
    assert "11:00–18:00" in problems[0]["problem"]


def test_when_the_model_moves_the_visit_the_warning_disappears():
    run = Run(replies=[_plan_json(_days()), _plan_json(_days("11:00", "12:00"))], hours={"Walk 0": "Mo-Su 11:00-18:00"})
    run.go()

    assert _hours_warnings(run) == []
    assert run.persisted[0].days[0].items[0].start_time.hour == 11


def test_a_visit_running_past_closing_is_flagged_but_the_tolerance_is_respected():
    late = Run(replies=[_plan_json(_days("16:00", "18:00"))], hours={"Walk 0": "Mo-Su 09:00-17:00"})
    late.go()
    (warning,) = _hours_warnings(late)
    assert "until 18:00" in warning and "closes at 17:00" in warning

    within = Run(replies=[_plan_json(_days("16:00", "17:10"))], hours={"Walk 0": "Mo-Su 09:00-17:00"})
    within.go()
    assert _hours_warnings(within) == []


def test_no_data_is_unknown_not_closed():
    run = Run(replies=[_plan_json(_days())], hours={})
    state = run.go()

    assert "error" not in state and _hours_warnings(run) == []
    assert run.sources["opening_hours"] == "unavailable"
    assert len(run.llm_messages) == 1                              # nothing to repair, so no extra model call


def test_hours_the_parser_cannot_fully_understand_stay_silent():
    for unsupported in ("Jan-Mar Mo-Fr 09:00-17:00", "by appointment", "sunrise-sunset", "Mo-Fr 09:00+"):
        run = Run(replies=[_plan_json(_days("03:00", "04:00"))], hours={"Walk 0": unsupported})
        run.go()
        assert _hours_warnings(run) == [], unsupported


def test_a_failing_provider_never_fails_planning():
    run = Run(replies=[_plan_json(_days())], hours_error=RuntimeError("overpass exploded"))
    state = run.go()

    assert "error" not in state and run.persisted is not None
    assert _hours_warnings(run) == [] and run.sources["opening_hours"] == "unavailable"


def test_a_slow_provider_is_cut_off_and_planning_still_completes():
    run = Run(replies=[_plan_json(_days())], hours={"Walk 0": "Mo-Su 11:00-18:00"}, timeout=0.05, delay=1.0)
    state = run.go()

    assert "error" not in state and run.persisted is not None
    assert _hours_warnings(run) == []


def test_when_the_feature_is_off_nothing_is_looked_up_and_nothing_is_reported():
    run = Run(replies=[_plan_json(_days())], hours={"Walk 0": "Mo-Su 11:00-18:00"}, enabled=False)
    run.go()

    assert run.hours_calls == [] and _hours_warnings(run) == []
    assert "opening_hours" not in run.sources                      # no misleading "unavailable" for a feature that is simply off


def test_lookups_per_plan_are_capped():
    days = [[{"item_type": "activity", "title": f"Spot {n}-{m}", "location_name": f"Place {n}-{m}, Paris",
              "start_time": f"{9 + m}:00", "end_time": f"{9 + m}:45"} for m in range(3)] for n in range(3)]
    run = Run(replies=[_plan_json(days)], max_lookups=4)
    run.go()

    assert len(run.hours_calls) == 4                              # 9 eligible venues, 4 allowed


def test_only_timed_located_venues_are_looked_up():
    days = [
        [{"item_type": "hotel", "title": "Some Hotel", "location_name": "Hotel, Paris"},
         {"item_type": "activity", "title": "Untimed Walk", "location_name": "Park, Paris"},
         {"item_type": "transport", "title": "Metro", "location_name": "Metro, Paris", "start_time": "09:00", "end_time": "09:30"},
         {"item_type": "restaurant", "title": "Bistro Nord", "location_name": "Bistro, Paris", "start_time": "12:00", "end_time": "13:00"}],
        [{"item_type": "attraction", "title": "Old Tower", "location_name": "Tower, Paris", "start_time": "10:00", "end_time": "11:00"}],
        [{"item_type": "note", "title": "Pack", "start_time": "08:00"}],
    ]
    run = Run(replies=[_plan_json(days)])
    run.go()

    assert sorted(run.hours_calls) == ["Bistro Nord", "Old Tower"]


def test_the_same_venue_on_two_days_is_looked_up_once():
    days = [[{"item_type": "attraction", "title": "Old Tower", "location_name": "Tower, Paris",
              "start_time": "10:00", "end_time": "11:00"}] for _ in range(3)]
    run = Run(replies=[_plan_json(days)], hours={"Old Tower": "Mo-Su 09:00-18:00"})
    run.go()

    assert run.hours_calls == ["Old Tower"]
    assert _hours_warnings(run) == []


def test_a_closed_weekday_is_reported_for_the_right_day():
    # 2026-10-01 is a Thursday: a venue that is closed Thursdays conflicts on day 1 only.
    run = Run(replies=[_plan_json(_days())], hours={"Walk 0": "Fr-We 09:00-18:00"})
    run.go()

    (warning,) = _hours_warnings(run)
    assert "day 1 (Thursday)" in warning and "no opening hours that day" in warning
