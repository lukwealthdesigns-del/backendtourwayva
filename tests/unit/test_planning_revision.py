"""Conversational revision: ORM->plan conversion, diff/summary, and the workflow."""
from __future__ import annotations

import asyncio
import json
from datetime import date, time
from types import SimpleNamespace as NS

from app.core.constants import TripItemType
from app.modules.planning.domain import GeoPoint, PlannedDay, PlannedItem, PlannedTrip, TripSpec
from app.modules.planning.revision import (
    build_revision_spec,
    catalog_from_plan,
    diff_plans,
    plan_from_db,
    revision_ports,
    summarize_diff,
)
from app.modules.planning.workflow import run_locally

SPEC = TripSpec(destination="Paris", start_date=date(2026, 10, 1), end_date=date(2026, 10, 3), travelers=2,
                budget_amount=2000.0, budget_currency="EUR")


def db_item(title, kind=TripItemType.ACTIVITY, **over):
    base = dict(id=title, item_type=kind, title=title, description=None, location_name=f"{title}, Paris",
                latitude=48.86, longitude=2.35, start_time=time(10), end_time=time(11), estimated_cost=30.0,
                currency="EUR", provider="ai", source="estimated", external_id=None, booking_link=None,
                image_url=None, notes=None)
    base.update(over)
    return NS(**base)


def db_days():
    hotel = db_item("Hotel Lumiere", TripItemType.HOTEL, provider="amadeus", source="provider", external_id="HX1",
                    start_time=None, end_time=None, estimated_cost=600.0, latitude=48.86, longitude=2.34)
    cruise = db_item("Seine Cruise", provider="amadeus", source="provider", external_id="AX1", estimated_cost=40.0,
                     booking_link="https://book/x", image_url="https://img/x")
    return [
        (NS(day_number=1, date=date(2026, 10, 1), weather_summary=None), [hotel, db_item("Louvre")]),
        (NS(day_number=2, date=date(2026, 10, 2), weather_summary="Sunny"), [cruise, db_item("Orsay", start_time=time(14), end_time=time(16))]),
        (NS(day_number=3, date=date(2026, 10, 3), weather_summary=None), [db_item("Montmartre Walk"), db_item("Bistro", TripItemType.RESTAURANT, start_time=time(19), end_time=time(21), estimated_cost=90.0)]),
    ]


TRIP = NS(budget_currency="EUR", overview="A lovely trip.")


def base_plan():
    return plan_from_db(TRIP, db_days())


# ---------------------------------------------------------------------------
def test_plan_from_db_keeps_provider_provenance_and_drops_internal_labels():
    plan = base_plan()
    hotel = plan.days[0].items[0]
    assert (hotel.item_type, hotel.source, hotel.external_id, hotel.provider) == ("hotel", "provider", "HX1", "amadeus")
    louvre = plan.days[0].items[1]
    assert louvre.source == "estimated" and louvre.external_id is None and louvre.provider is None   # "ai" is not a provider
    assert plan.currency == "EUR" and plan.overview == "A lovely trip." and plan.days[1].weather_summary == "Sunny"


def test_catalog_from_plan_lets_a_revision_keep_verified_items_untouched():
    catalog = catalog_from_plan(base_plan())
    assert set(catalog) == {"HX1", "AX1"}
    assert catalog["HX1"]["kind"] == "hotel" and catalog["HX1"]["hotel_name"] == "Hotel Lumiere" and catalog["HX1"]["group_cost"] == 600.0
    assert catalog["AX1"]["kind"] == "activity" and catalog["AX1"]["booking_link"] == "https://book/x"


def test_diff_detects_removed_added_moved_retimed_and_cost_change():
    before = base_plan()
    after = base_plan()
    after.days[2].items = [i for i in after.days[2].items if i.title != "Bistro"]           # removed
    after.days[1].items.append(PlannedItem("activity", "Rodin Museum", estimated_cost=12.0, currency="EUR"))   # added
    moved = after.days[0].items.pop(1)                                                       # Louvre: day 1 -> day 2
    after.days[1].items.append(moved)
    after.days[1].items[1].start_time = time(15)                                             # Orsay retimed

    diff = diff_plans(before, after)
    assert [t for _, t in diff.removed] == ["Bistro"] and [t for _, t in diff.added] == ["Rodin Museum"]
    assert diff.moved == [("Louvre", 1, 2)] and diff.retimed == [(2, "Orsay")]
    assert diff.cost_before - diff.cost_after == 78.0        # 90 removed, 12 added
    assert not diff.is_empty


def test_identical_plans_have_an_empty_diff():
    assert diff_plans(base_plan(), base_plan()).is_empty


def test_summary_is_readable_and_bounded():
    before, after = base_plan(), base_plan()
    for day in after.days:
        day.items = [i for i in day.items if i.item_type == "hotel"]
    text = summarize_diff(diff_plans(before, after), "make it cheaper")
    assert text.startswith('Revise trip: "make it cheaper"') and "removes" in text and "→" in text
    assert len(summarize_diff(diff_plans(before, after), "x" * 900)) <= 480


# ---------------------------------------------------------------------------
# Workflow
# ---------------------------------------------------------------------------
def plan_json(base, mutate):
    data = json.loads(json.dumps({
        "overview": "Revised.", "days": [{"day_number": d.day_number, "items": [
            {"item_type": i.item_type, "title": i.title, "location_name": i.location_name,
             "start_time": i.start_time.strftime("%H:%M") if i.start_time else None,
             "end_time": i.end_time.strftime("%H:%M") if i.end_time else None,
             "estimated_cost": i.estimated_cost, "external_id": i.external_id} for i in d.items]} for d in base.days]}))
    mutate(data)
    return json.dumps(data)


class Harness:
    def __init__(self, replies):
        self.replies, self.llm_calls, self.stored = list(replies), 0, None

    def run(self, instruction="make it cheaper", geo=True):
        async def geocode(query):
            return GeoPoint(latitude=48.86, longitude=2.35, formatted_address=query)

        async def llm(messages, temperature, max_tokens):
            self.llm_calls += 1
            return self.replies.pop(0) if len(self.replies) > 1 else self.replies[0]

        async def store(plan, meta):
            self.stored = (plan, meta)
            return {"changed": True}

        base = base_plan()
        state = {"spec": SPEC, "currency": "EUR", "language": "en", "base_plan": base, "instruction": instruction,
                 "catalog": catalog_from_plan(base), "allow_empty_days": True, "hotels_for_model": [],
                 "activities_for_model": [], "forecast": [], "data_sources": {}, "repair_attempts": 0,
                 "repairs": [], "notes": [], "issues": []}
        if geo:
            state["geo"] = GeoPoint(latitude=48.8566, longitude=2.3522)
        spec = build_revision_spec(revision_ports(geocode=geocode, llm=llm, store=store))
        return asyncio.run(run_locally(spec, state))


def test_revision_that_removes_an_item_is_validated_and_stored_as_a_proposal():
    base = base_plan()
    h = Harness([plan_json(base, lambda d: d["days"][2]["items"].pop())])          # drop the dinner
    state = h.run()
    plan, _ = h.stored
    assert "error" not in state and h.llm_calls == 1
    assert "Bistro" not in [i.title for d in plan.days for i in d.items]
    assert diff_plans(base, plan).removed == [(3, "Bistro")]


def test_verified_provider_items_survive_a_revision_with_their_stored_data():
    base = base_plan()

    def tamper(d):                                    # the model tries to cheapen the hotel and cruise
        d["days"][0]["items"][0]["estimated_cost"] = 1.0
        d["days"][1]["items"][0]["estimated_cost"] = 1.0
    h = Harness([plan_json(base, tamper)])
    h.run()
    plan, _ = h.stored
    hotel = next(i for i in plan.days[0].items if i.item_type == "hotel")
    cruise = next(i for i in plan.days[1].items if i.title == "Seine Cruise")
    assert hotel.estimated_cost == 600.0 and hotel.source == "provider" and hotel.external_id == "HX1"
    assert cruise.estimated_cost == 40.0 and cruise.booking_link == "https://book/x"


def test_removing_a_whole_day_leaves_a_free_day_instead_of_failing():
    base = base_plan()
    h = Harness([plan_json(base, lambda d: d["days"][2].update(items=[]))])
    state = h.run("remove day 3")
    plan, meta = h.stored
    assert "error" not in state and plan.days[2].items == []
    assert any("Day 3 has nothing planned" in w for w in meta["warnings"])


def test_an_invalid_revision_is_repaired_or_rejected_never_stored():
    base = base_plan()

    def overlap(d):                                   # two timed items now clash
        d["days"][1]["items"][1].update(start_time="10:30", end_time="12:00")
    fixed = Harness([plan_json(base, overlap)])
    assert "error" not in fixed.run() and fixed.llm_calls == 1 and fixed.stored is not None

    def impossible(d):                                # hotel alone now exceeds the whole budget
        d["days"][0]["items"][0].update(estimated_cost=5000, external_id=None)
    h = Harness([plan_json(base, impossible)])
    state = h.run()
    assert state["error"]["code"] == "plan_invalid" and h.stored is None


def test_unusable_output_fails_without_storing():
    h = Harness(["not json at all"])
    state = h.run()
    assert state["error"]["code"] == "ai_output_invalid" and h.stored is None and h.llm_calls == 2
