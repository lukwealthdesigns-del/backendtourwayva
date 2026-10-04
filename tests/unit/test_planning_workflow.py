"""
The itinerary-generation graph, run end to end with fake ports (no network,
no database, no LangGraph). The same WorkflowSpec is what LangGraph executes
in production; `run_locally` has identical semantics.
"""
from __future__ import annotations

import asyncio
import json
from datetime import date, time

import pytest

from app.core.exceptions import ProviderUnavailableError
from app.modules.planning.domain import GeoPoint, TripSpec
from app.modules.planning.graph import PlanningPorts, build_generation_spec
from app.modules.planning.workflow import WorkflowSpec, run_locally

PARIS = GeoPoint(latitude=48.8566, longitude=2.3522, formatted_address="Paris, France", country="FR", city="Paris")
SPEC = TripSpec(destination="Paris", start_date=date(2026, 10, 1), end_date=date(2026, 10, 3),
                travelers=2, budget_amount=2000.0, budget_currency="EUR")
TODAY = date(2026, 9, 25)

HOTEL = {"hotel_id": "HX1", "hotel_name": "Hotel Lumiere", "offer_id": "O1", "latitude": 48.86, "longitude": 2.34,
         "room_description": "Double room", "price_total": 600.0, "currency": "EUR", "provider": "amadeus"}
ACTIVITY = {"activity_id": "AX1", "name": "Seine Cruise", "description": "1h cruise", "latitude": 48.858,
            "longitude": 2.294, "price_amount": 20.0, "currency": "EUR", "picture_url": "https://img/x.jpg",
            "booking_link": "https://book/x", "provider": "amadeus"}


def plan_json(days):
    return json.dumps({"overview": "A lovely trip.", "days": [{"day_number": i + 1, "items": items}
                                                              for i, items in enumerate(days)]})


def good_days():
    return [
        [{"item_type": "hotel", "title": "Some Hotel", "external_id": "h1", "estimated_cost": 1.0},
         {"item_type": "activity", "title": "Louvre", "location_name": "Louvre Museum, Paris",
          "start_time": "09:30", "end_time": "12:00", "estimated_cost": 40, "must_see": True}],
        [{"item_type": "activity", "title": "Cruise", "external_id": "a1", "start_time": "10:00",
          "end_time": "11:00", "estimated_cost": 999, "outdoor": True}],
        [{"item_type": "restaurant", "title": "Le Petit Bistro", "location_name": "Bistro, Paris",
          "start_time": "12:00", "end_time": "13:30", "estimated_cost": 60}],
    ]


class Harness:
    def __init__(self, *, llm_replies, hotels=(HOTEL,), activities=(ACTIVITY,), forecast=None,
                 geocode_fail=(), rates=None, spec=SPEC, city_code="PAR"):
        self.llm_replies = list(llm_replies)
        self.llm_calls = 0
        self.persisted = None
        self.forecast_calls = 0
        self.spec = spec
        self.city_code = city_code
        self._hotels, self._activities, self._forecast = hotels, activities, forecast or []
        self.geocode_fail = set(geocode_fail)
        self.rates = rates if rates is not None else {"EUR": 1.0}

    def ports(self):
        async def geocode(query):
            if query in self.geocode_fail:
                raise LookupError("not found")
            if query == "Paris":
                return PARIS
            # a nearby verified point for any other place
            return GeoPoint(latitude=48.86, longitude=2.35, formatted_address=query)

        async def search_hotels(city, a, b, adults):
            if isinstance(self._hotels, Exception):
                raise self._hotels
            return [dict(h) for h in self._hotels]

        async def search_activities(lat, lon):
            return [dict(a) for a in self._activities]

        async def forecast(lat, lon, days):
            self.forecast_calls += 1
            return self._forecast

        async def convert_rate(base, target):
            if base not in self.rates:
                raise ProviderUnavailableError("no rate")
            return self.rates[base]

        async def llm(messages, temperature, max_tokens):
            self.llm_calls += 1
            reply = self.llm_replies.pop(0) if len(self.llm_replies) > 1 else self.llm_replies[0]
            if isinstance(reply, Exception):
                raise reply
            return reply

        async def persist(plan, meta):
            self.persisted = (plan, meta)
            return {"version_number": 1}

        return PlanningPorts(geocode=geocode, search_hotels=search_hotels, search_activities=search_activities,
                             forecast=forecast, convert_rate=convert_rate, llm=llm, persist=persist,
                             today=lambda: TODAY)

    def run(self):
        state = {"spec": self.spec, "currency": "EUR", "preferences": {}, "memories": [],
                 "options": {"city_code": self.city_code}}
        return asyncio.run(run_locally(build_generation_spec(self.ports()), state))


def filler_days():
    return [[{"item_type": "activity", "title": f"Walk {n}", "location_name": f"Park {n}, Paris",
              "start_time": "10:00", "end_time": "11:00"}] for n in range(3)]


def by_type(plan, item_type):
    return [i for d in plan.days for i in d.items if i.item_type == item_type]


def _titles(plan):
    return [i.title for d in plan.days for i in d.items]


# ---------------------------------------------------------------------------
def test_happy_path_grounds_provider_items_and_verifies_locations():
    h = Harness(llm_replies=[plan_json(good_days())])
    state = h.run()

    assert "error" not in state and state["result"] == {"version_number": 1}
    plan, meta = h.persisted
    hotel = by_type(plan, "hotel")[0]
    assert hotel.title == "Hotel Lumiere" and hotel.estimated_cost == 600.0 and hotel.source == "provider"
    assert hotel.provider == "amadeus" and (hotel.latitude, hotel.longitude) == (48.86, 2.34)
    assert hotel.external_id == "HX1"                          # the PROVIDER's id, not the short "h1" the model saw
    cruise = plan.days[1].items[0]
    assert cruise.estimated_cost == 40.0                       # 20 per person x 2 travellers, NOT the model's 999
    assert cruise.booking_link == "https://book/x" and cruise.image_url == "https://img/x.jpg"
    assert cruise.external_id == "AX1"
    louvre = next(i for i in plan.days[0].items if i.title == "Louvre")
    assert louvre.source == "estimated" and louvre.latitude == 48.86   # location verified by geocoding
    assert meta["data_sources"] == {"currency": "ok", "hotels": "ok", "activities": "ok", "weather": "ok"}
    assert h.llm_calls == 1


def test_model_cannot_set_the_price_of_a_provider_item():
    days = good_days()
    days[0][0]["estimated_cost"] = 1.0                          # claims the hotel costs 1
    h = Harness(llm_replies=[plan_json(days)])
    h.run()
    assert by_type(h.persisted[0], "hotel")[0].estimated_cost == 600.0


def test_unknown_option_reference_is_downgraded_to_an_estimate():
    days = good_days()
    days[1][0]["external_id"] = "a99"                           # not in the verified catalogue
    h = Harness(llm_replies=[plan_json(days)])
    h.run()
    cruise = h.persisted[0].days[1].items[0]
    assert cruise.source == "estimated" and cruise.external_id is None and cruise.provider is None
    assert any("unrecognised" in n for n in h.persisted[1]["notes"])


def test_repeated_hotel_entries_are_collapsed():
    days = good_days()
    days[1].insert(0, {"item_type": "hotel", "title": "Hotel again", "external_id": "h1"})
    h = Harness(llm_replies=[plan_json(days)])
    h.run()
    plan = h.persisted[0]
    assert [i.item_type for d in plan.days for i in d.items].count("hotel") == 1


def test_provider_outage_is_reported_and_nothing_is_invented():
    days = good_days()
    days[0] = [i for i in days[0] if i["item_type"] != "hotel"]
    h = Harness(llm_replies=[plan_json(days)], hotels=ProviderUnavailableError("amadeus down"))
    state = h.run()
    assert h.persisted[1]["data_sources"]["hotels"] == "unavailable"
    assert any("accommodation" in w for w in h.persisted[1]["warnings"])
    assert "error" not in state


def test_missing_exchange_rate_drops_the_price_rather_than_guessing():
    foreign = {**ACTIVITY, "currency": "GBP"}
    h = Harness(llm_replies=[plan_json(good_days())], activities=(foreign,), rates={"EUR": 1.0})
    h.run()
    assert h.persisted[0].days[1].items[0].estimated_cost is None
    assert h.persisted[1]["data_sources"]["currency"] == "unavailable"


def test_hotels_skipped_without_a_city_code():
    h = Harness(llm_replies=[plan_json(filler_days())], city_code="")
    h.run()
    assert h.persisted[1]["data_sources"]["hotels"] == "skipped"


def test_destination_not_found_stops_before_any_ai_call_or_write():
    h = Harness(llm_replies=[plan_json(good_days())], geocode_fail={"Paris"})
    state = h.run()
    assert state["error"]["code"] == "destination_not_found"
    assert h.llm_calls == 0 and h.persisted is None


def test_trip_longer_than_14_days_is_rejected():
    long_spec = TripSpec(destination="Paris", start_date=date(2026, 10, 1), end_date=date(2026, 10, 20))
    h = Harness(llm_replies=["{}"], spec=long_spec)
    assert h.run()["error"]["code"] == "trip_too_long" and h.llm_calls == 0


def test_unusable_model_output_fails_without_persisting():
    h = Harness(llm_replies=["this is not json"])
    state = h.run()
    assert state["error"]["code"] == "ai_output_invalid" and state["error"]["retryable"]
    assert h.llm_calls == 2 and h.persisted is None             # one retry, then give up


def test_ai_provider_outage_is_a_retryable_error():
    h = Harness(llm_replies=[ProviderUnavailableError("no key")])
    state = h.run()
    assert state["error"]["code"] == "ai_unavailable" and state["error"]["retryable"] and h.persisted is None


def test_deterministic_repair_fixes_clashes_without_asking_the_model_again():
    days = good_days()
    days[0][1].update(start_time="09:30", end_time="12:00")
    days[0].append({"item_type": "activity", "title": "Orsay", "location_name": "Musee d'Orsay, Paris",
                    "start_time": "11:00", "end_time": "13:00", "estimated_cost": 30})     # overlaps the Louvre
    h = Harness(llm_replies=[plan_json(days)])
    state = h.run()
    assert "error" not in state and h.llm_calls == 1
    assert state["repairs"] and h.persisted[1]["repairs"] == state["repairs"]
    starts = [i.start_time for i in h.persisted[0].days[0].items if i.start_time]
    assert starts == sorted(starts)


def test_over_budget_is_trimmed_deterministically():
    days = good_days()
    days[2][0]["estimated_cost"] = 1500                          # an expensive dinner pushes the plan over 2000
    days[2].append({"item_type": "activity", "title": "Evening walk", "location_name": "Seine, Paris",
                    "start_time": "16:00", "end_time": "17:00"})
    h = Harness(llm_replies=[plan_json(days)])
    state = h.run()
    plan = h.persisted[0]
    assert "error" not in state and sum(i.estimated_cost or 0 for d in plan.days for i in d.items) <= 2000
    assert "Le Petit Bistro" not in _titles(plan) and "Hotel Lumiere" in _titles(plan)


def test_unfixable_plan_asks_the_model_twice_then_fails_and_never_saves():
    days = good_days()
    days[0][0] = {"item_type": "hotel", "title": "Palace", "estimated_cost": 5000}   # the hotel alone blows the budget
    h = Harness(llm_replies=[plan_json(days)], hotels=())
    state = h.run()
    assert state["error"]["code"] == "plan_invalid" and h.persisted is None
    assert h.llm_calls == 3                                      # draft + two repair rounds, then stop


def test_model_repair_can_rescue_a_plan_with_the_wrong_number_of_days():
    bad = plan_json(good_days()[:2])                             # only 2 of 3 days
    fixed = plan_json(good_days())
    h = Harness(llm_replies=[bad, fixed])
    state = h.run()
    assert "error" not in state and len(h.persisted[0].days) == 3 and h.llm_calls == 2


def test_weather_conflict_gets_one_repair_attempt_and_never_loops():
    rainy = [{"date": "2026-10-02", "condition": "Heavy rain", "chance_of_rain_pct": 90, "high_c": 14, "low_c": 9}]
    same = plan_json(good_days())                                # outdoor cruise on the rainy day 2
    h = Harness(llm_replies=[same, same], forecast=rainy)
    state = h.run()
    assert "error" not in state and h.llm_calls == 2             # draft + exactly one revision attempt
    assert any("wet" in w for w in h.persisted[1]["warnings"])   # reported, not hidden
    assert h.persisted[0].days[1].weather_summary.startswith("Heavy rain")


def test_no_forecast_is_requested_beyond_the_provider_horizon():
    far = TripSpec(destination="Paris", start_date=date(2026, 12, 1), end_date=date(2026, 12, 3), travelers=2)
    days = plan_json(filler_days())
    h = Harness(llm_replies=[days], spec=far)
    h.run()
    assert h.forecast_calls == 0 and h.persisted[1]["data_sources"]["weather"] == "skipped"


def test_generation_graph_is_wired_correctly_and_miswiring_is_caught():
    build_generation_spec(Harness(llm_replies=["{}"]).ports())          # validates on build
    async def noop(state): return {}
    with pytest.raises(ValueError):
        WorkflowSpec(entry="a", nodes={"a": noop, "b": noop}, edges=[("a", "b")]).validate()   # b has no exit
    with pytest.raises(ValueError):
        WorkflowSpec(entry="x", nodes={"a": noop}, finish=["a"]).validate()                      # unknown entry


# ---------------------------------------------------------------------------
# Real LangGraph (skipped automatically when the package is not installed)
# ---------------------------------------------------------------------------
def _state(h):
    return {"spec": h.spec, "currency": "EUR", "language": "en", "preferences": {}, "memories": [],
            "options": {"city_code": h.city_code}, "repair_attempts": 0, "repairs": [], "notes": [], "issues": []}


def test_langgraph_runs_the_same_spec_end_to_end():
    pytest.importorskip("langgraph")
    from app.modules.planning.graph import PlanningState
    from app.modules.planning.workflow import run_workflow

    h = Harness(llm_replies=[plan_json(good_days())])
    state = asyncio.run(run_workflow(build_generation_spec(h.ports()), PlanningState, _state(h)))
    assert state["result"] == {"version_number": 1} and h.persisted is not None


def test_langgraph_and_local_runner_agree_on_the_repair_loop():
    pytest.importorskip("langgraph")
    from app.modules.planning.graph import PlanningState
    from app.modules.planning.workflow import run_workflow

    days = good_days()
    days[0][0] = {"item_type": "hotel", "title": "Palace", "estimated_cost": 5000}       # unfixable budget
    lg = Harness(llm_replies=[plan_json(days)], hotels=())
    local = Harness(llm_replies=[plan_json(days)], hotels=())
    lg_state = asyncio.run(run_workflow(build_generation_spec(lg.ports()), PlanningState, _state(lg)))
    local_state = local.run()
    assert lg_state["error"]["code"] == local_state["error"]["code"] == "plan_invalid"
    assert lg.llm_calls == local.llm_calls == 3 and lg.persisted is None


# ---------------------------------------------------------------------------
# IATA city-code auto-resolution and hotel image enrichment (Master Prompt §21)
# ---------------------------------------------------------------------------
def test_hotels_are_searched_even_without_a_client_supplied_city_code_via_auto_resolution():
    resolved = []

    async def resolve_city_code(destination):
        resolved.append(destination)
        return "par"    # lowercase on purpose: the graph must upper() it

    h = Harness(llm_replies=[plan_json(good_days())], city_code="")
    ports = h.ports()
    ports.resolve_city_code = resolve_city_code
    state = asyncio.run(run_locally(build_generation_spec(ports), {
        "spec": h.spec, "currency": "EUR", "preferences": {}, "memories": [], "options": {"city_code": ""},
    }))
    assert resolved == [h.spec.destination]
    assert "error" not in state
    assert by_type(h.persisted[0], "hotel")


def test_a_failed_city_code_resolution_just_skips_hotels_like_an_empty_code_today():
    async def resolve_city_code(destination):
        raise RuntimeError("amadeus down")

    h = Harness(llm_replies=[plan_json(good_days())], city_code="")
    ports = h.ports()
    ports.resolve_city_code = resolve_city_code

    async def failing_search(*a, **k):
        raise AssertionError("search_hotels must not be called when no code was resolved")

    ports.search_hotels = failing_search
    state = asyncio.run(run_locally(build_generation_spec(ports), {
        "spec": h.spec, "currency": "EUR", "preferences": {}, "memories": [], "options": {"city_code": ""},
    }))
    assert "error" not in state


def test_an_explicit_city_code_skips_resolution_entirely():
    called = []

    async def resolve_city_code(destination):
        called.append(destination)
        return "XXX"

    h = Harness(llm_replies=[plan_json(good_days())], city_code="PAR")
    ports = h.ports()
    ports.resolve_city_code = resolve_city_code
    h.run()
    assert called == []


def test_the_top_hotels_are_enriched_with_an_image_and_it_lands_on_the_trip_item():
    image_calls = []

    async def image(name):
        image_calls.append(name)
        return {"url": f"https://img/{name}"}

    hotel_a = {**HOTEL, "hotel_id": "HA", "hotel_name": "Grand Hotel", "price_total": 100.0}
    hotel_b = {**HOTEL, "hotel_id": "HB", "hotel_name": "Budget Inn", "price_total": 50.0}
    h = Harness(llm_replies=[plan_json(good_days())], hotels=[hotel_a, hotel_b])
    ports = h.ports()
    ports.image = image
    asyncio.run(run_locally(build_generation_spec(ports), {
        "spec": h.spec, "currency": "EUR", "preferences": {}, "memories": [], "options": {"city_code": "PAR"},
    }))
    hotel_item = by_type(h.persisted[0], "hotel")[0]
    assert hotel_item.image_url == f"https://img/{hotel_item.title}"
    assert hotel_item.title in image_calls


def test_a_failed_image_lookup_never_blocks_planning():
    async def image(name):
        raise RuntimeError("unsplash down")

    h = Harness(llm_replies=[plan_json(good_days())])
    ports = h.ports()
    ports.image = image
    state = asyncio.run(run_locally(build_generation_spec(ports), {
        "spec": h.spec, "currency": "EUR", "preferences": {}, "memories": [], "options": {"city_code": "PAR"},
    }))
    assert "error" not in state
    assert by_type(h.persisted[0], "hotel")[0].image_url is None


def test_only_the_top_hotels_are_enriched_bounding_image_search_calls():
    from app.modules.planning.graph import MAX_HOTEL_IMAGES

    image_calls = []

    async def image(name):
        image_calls.append(name)
        return None

    hotels = [{**HOTEL, "hotel_id": f"H{n}", "hotel_name": f"Hotel {n}", "price_total": float(n * 10)} for n in range(1, 6)]
    h = Harness(llm_replies=[plan_json(good_days())], hotels=hotels)
    ports = h.ports()
    ports.image = image
    asyncio.run(run_locally(build_generation_spec(ports), {
        "spec": h.spec, "currency": "EUR", "preferences": {}, "memories": [], "options": {"city_code": "PAR"},
    }))
    assert len(image_calls) == MAX_HOTEL_IMAGES
