"""The Discover workflow end to end with fake ports (no network/DB/LangGraph)."""
from __future__ import annotations

import asyncio
import json
from datetime import date, timedelta
from types import SimpleNamespace as NS

import pytest

from app.core.exceptions import ProviderUnavailableError
from app.modules.discover.graph import EXCLUDE_FLIGHTS_NOTE, DiscoverPorts, DiscoverState, build_discover_spec
from app.modules.planning.workflow import run_locally

TODAY = date(2026, 9, 25)
REQUEST = {"budget_amount": 700000.0, "budget_currency": "NGN", "duration_days": 7, "travelers": 2,
           "interests": [], "max_results": 3}


def cand(name, cc, daily, period="April to June", reasons="A lovely place."):
    return {"destination": name, "country_code": cc, "reasons": reasons, "estimated_daily_cost_usd": daily,
            "best_travel_period": period}


DEFAULT = [cand("Lisbon", "PT", 30), cand("Paris", "FR", 60), cand("Cairo", "EG", 25), cand("Kigali", "RW", 28)]


class Harness:
    def __init__(self, *, candidates=DEFAULT, replies=None, geo=None, rate=1500.0, cache=None, profile=None,
                 forecast=None):
        self.llm_calls = 0
        self.replies = replies if replies is not None else [json.dumps(candidates)]
        self.geo_fail, self.geo_country = set(), geo or {}
        self.rate, self.cache, self.profile = rate, cache, profile or {}
        self.forecast_calls = []
        self.persisted = None
        self.rate_calls = 0
        self.in_flight = self.max_in_flight = 0
        self._forecast = forecast or []

    def ports(self):
        async def llm(messages, temperature, max_tokens):
            self.llm_calls += 1
            reply = self.replies.pop(0) if len(self.replies) > 1 else self.replies[0]
            if isinstance(reply, Exception):
                raise reply
            return reply

        async def geocode(query):
            self.in_flight += 1
            self.max_in_flight = max(self.max_in_flight, self.in_flight)
            await asyncio.sleep(0)
            self.in_flight -= 1
            if query in self.geo_fail:
                raise LookupError(query)
            return NS(latitude=10.0, longitude=20.0, country=self.geo_country.get(query))

        async def forecast(lat, lon, days):
            self.forecast_calls.append(days)
            return self._forecast

        async def image(dest):
            return {"url": f"https://img/{dest}", "attribution": "Photo by A on Unsplash"}

        async def activities(lat, lon):
            return ["Old town tour", "Boat trip"]

        async def usd_rate(currency):
            self.rate_calls += 1
            if isinstance(self.rate, Exception):
                raise self.rate
            return self.rate

        async def load_profile():
            return self.profile

        async def cache_get(key):
            return self.cache

        async def persist(results, meta):
            self.persisted = (results, meta)
            return {"search_id": "s-1", "results": results, "source": meta["source"], "notes": meta["notes"]}

        return DiscoverPorts(llm=llm, geocode=geocode, forecast=forecast, image=image, activities=activities,
                             usd_rate=usd_rate, load_profile=load_profile, cache_get=cache_get, persist=persist,
                             today=lambda: TODAY)

    def run(self, **request):
        state = {"request": {**REQUEST, **request}}
        return asyncio.run(run_locally(build_discover_spec(self.ports()), state))


def names(state):
    return [r["destination"] for r in state["outcome"]["results"]]


# ---------------------------------------------------------------------------
def test_live_search_costs_ranks_and_flags_over_budget_in_the_budget_currency():
    h = Harness()
    state = h.run(max_results=4)
    results = state["outcome"]["results"]

    assert state["outcome"]["source"] == "live" and len(results) == 4
    lisbon = next(r for r in results if r["destination"] == "Lisbon")
    assert lisbon["cost_breakdown"]["total"] == 630000.0                             # 30 USD x 7 days x 2 people x 1500
    assert lisbon["cost_breakdown"]["currency"] == "NGN" and lisbon["cost_breakdown"]["source"] == "estimated"
    assert lisbon["over_budget"] is False
    assert next(r for r in results if r["destination"] == "Paris")["over_budget"] is True     # 60 USD/day = 1.26M
    assert results[-1]["destination"] == "Paris"                                     # the over-budget one ranks last
    assert lisbon["image_url"] == "https://img/Lisbon" and lisbon["relevant_activities"] == ["Old town tour", "Boat trip"]
    assert lisbon["trip_planning_cta"]["budget_amount"] == 700000.0                  # the USER's budget, not the estimate
    assert lisbon["trip_planning_cta"]["estimated_cost"] == 630000.0
    assert EXCLUDE_FLIGHTS_NOTE in state["outcome"]["notes"]
    assert h.llm_calls == 1 and h.rate_calls == 1                                    # ONE rate, not one per candidate


def test_results_are_trimmed_to_max_results_and_the_over_budget_option_is_what_gets_cut():
    state = Harness().run()                                                          # 4 verified candidates, max_results=3
    assert len(state["outcome"]["results"]) == 3 and "Paris" not in names(state)


def test_the_full_ranked_list_is_handed_to_persist_for_caching_untrimmed():
    h = Harness()
    h.run()
    results, meta = h.persisted
    assert len(results) == 3 and len(meta["cache_payload"]) == 4 and meta["cache_key"].startswith("discover:v2:")


def test_a_cache_hit_skips_the_model_and_the_rate_call():
    cached = [{"destination": "Lisbon", "score": 0.9}, {"destination": "Cairo", "score": 0.8}]
    h = Harness(cache=cached)
    state = h.run()
    assert state["outcome"]["source"] == "cache" and names(state) == ["Lisbon", "Cairo"]
    assert h.llm_calls == 0 and h.rate_calls == 0


def test_visited_places_are_filtered_after_the_cache_so_history_never_leaks_between_users():
    cached = [{"destination": "Lisbon, Portugal", "score": 0.9}, {"destination": "Cairo", "score": 0.8}]
    h = Harness(cache=cached, profile={"visited": ["Lisbon"]})
    state = h.run()
    assert names(state) == ["Cairo"] and any("already visited: Lisbon, Portugal" in n for n in state["outcome"]["notes"])
    assert len(h.persisted[1]["cache_payload"]) == 2                                 # the shared list keeps Lisbon

    kept = Harness(cache=cached, profile={"visited": ["Lisbon"]}).run(exclude_visited=False)
    assert names(kept) == ["Lisbon, Portugal", "Cairo"]


def test_saved_preferences_fill_gaps_and_are_reported():
    h = Harness(profile={"interests": ["food"], "travel_style": "relaxed"})
    state = h.run()
    assert "Used your saved interests." in state["outcome"]["notes"]
    assert state["constraints"]["interests"] == ["food"]
    explicit = Harness(profile={"interests": ["food"]}).run(interests=["hiking"])
    assert explicit["constraints"]["interests"] == ["hiking"] and "Used your saved interests." not in explicit["outcome"]["notes"]


def test_places_that_cannot_be_verified_are_dropped_never_invented():
    h = Harness(candidates=[cand("Atlantis", "XX", 20), cand("Lisbon", "PT", 30), cand("Georgetown", "GY", 20)],
                geo={"Lisbon": "PT", "Georgetown": "MY"})              # Georgetown geocodes to Malaysia, not Guyana
    h.geo_fail = {"Atlantis"}
    state = h.run()
    assert names(state) == ["Lisbon"]


def test_when_nothing_verifies_the_search_fails_instead_of_returning_nothing_silently():
    h = Harness(candidates=[cand("Atlantis", "XX", 20)])
    h.geo_fail = {"Atlantis"}
    state = h.run()
    assert state["error"]["code"] == "no_destinations_found" and h.persisted is None


def test_an_unavailable_exchange_rate_fails_honestly_before_any_ai_call():
    h = Harness(rate=ProviderUnavailableError("fx down"))
    state = h.run()
    assert state["error"]["code"] == "currency_unavailable" and state["error"]["retryable"]
    assert h.llm_calls == 0 and h.persisted is None                                   # no fabricated USD-as-naira fallback


def test_usd_budgets_need_no_rate():
    h = Harness()
    h.run(budget_currency="USD", budget_amount=5000.0)
    assert h.rate_calls == 0


def test_unusable_model_output_is_retried_once_then_reported():
    h = Harness(replies=["not json"])
    state = h.run()
    assert state["error"]["code"] == "ai_output_invalid" and h.llm_calls == 2 and h.persisted is None
    down = Harness(replies=[ProviderUnavailableError("no key")]).run()
    assert down["error"]["code"] == "ai_unavailable" and down["error"]["retryable"]


def test_a_zero_cost_estimate_can_never_win_the_ranking():
    h = Harness(candidates=[cand("Freeville", "PT", 0), cand("Lisbon", "PT", 30)])
    assert names(h.run()) == ["Lisbon"]


def test_forecast_is_used_only_for_trips_inside_the_forecast_horizon():
    soon = TODAY + timedelta(days=3)
    forecast = [{"date": (soon + timedelta(days=i)).isoformat(), "high_c": 24, "low_c": 16} for i in range(3)]
    near = Harness(forecast=forecast)
    state = near.run(start_date=soon.isoformat(), end_date=(soon + timedelta(days=2)).isoformat(), duration_days=3)
    assert near.forecast_calls and state["outcome"]["results"][0]["weather_summary"] == "Forecast 16-24°C for 3 of your trip days"

    far = Harness(forecast=forecast)
    far_state = far.run(start_date="2026-12-01", end_date="2026-12-07")
    assert far.forecast_calls == [] and far_state["outcome"]["results"][0]["weather_summary"] is None
    undated = Harness()
    undated.run()
    assert undated.forecast_calls == []


def test_being_in_season_lifts_a_destination():
    h = Harness(candidates=[cand("Off Season", "PT", 30, period="October to December"),
                            cand("In Season", "FR", 30, period="April to June")])
    state = h.run(start_date="2026-05-01", end_date="2026-05-07")
    assert names(state) == ["In Season", "Off Season"]
    assert state["outcome"]["results"][0]["season_fit"] == 1.0


def test_candidates_are_verified_concurrently_but_bounded():
    h = Harness(candidates=[cand(f"City {n}", "PT", 30) for n in range(7)])
    h.run(max_results=5)
    assert 2 <= h.max_in_flight <= 4


def test_the_graph_is_wired_correctly():
    build_discover_spec(Harness().ports())


def test_langgraph_runs_the_discover_spec():
    pytest.importorskip("langgraph")
    from app.modules.planning.workflow import run_workflow

    h = Harness()
    state = asyncio.run(run_workflow(build_discover_spec(h.ports()), DiscoverState, {"request": dict(REQUEST)}))
    assert state["outcome"]["source"] == "live" and len(state["outcome"]["results"]) == 3
