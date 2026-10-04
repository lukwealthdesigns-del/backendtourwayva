"""
The Discover pipeline (Master Prompt §11) as a LangGraph workflow:

    prepare_constraints ─► cache_lookup ─┬─(hit)──────────────────────────────┐
                                         └─(miss)─► normalize_currency ─► generate_candidates ─► enrich_candidates ─┤
                                                         │ fail                │ fail                                │
                                                         ▼                     ▼                                     ▼
                                                        fail ◄───────────────────────────────────── rank_results ─► persist

  prepare_constraints  effective constraints: the request wins, the user's saved
                       profile fills the gaps (and is reported back)
  cache_lookup         reuse before regenerate (§P3) — keyed on constraints only
  normalize_currency   ONE real USD→budget-currency rate; if it is unavailable the
                       search fails honestly (affordability cannot be judged)
  generate_candidates  the model PROPOSES destinations with a per-person/day estimate
  enrich_candidates    systems VERIFY, concurrently per candidate: geocoding (and
                       country agreement), forecast for the traveller's dates, image,
                       bookable activities — a candidate that does not check out is dropped
  rank_results         cost in the budget currency, budget/interest/season scoring,
                       validation, then personalization (already-visited) and trimming
  persist              DiscoverySearch/Results rows + cache

Depends only on `DiscoverPorts`, so it is testable with fakes.
"""
from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Any, Awaitable, Callable, Optional, TypedDict

from app.core.exceptions import ProviderUnavailableError
from app.modules.discover.logic import (
    EXTRA_CANDIDATES,
    CandidateParseError,
    build_candidate_messages,
    build_constraints,
    cache_key,
    country_matches,
    parse_candidates,
    same_place,
    season_fit,
    weather_summary,
)
from app.modules.discover.scoring import (
    build_cost_breakdown,
    compute_budget_fit_score,
    compute_interest_match_score,
    compute_overall_score,
    is_over_budget,
)
from app.modules.planning.workflow import WorkflowSpec

logger = logging.getLogger(__name__)

FORECAST_HORIZON_DAYS = 10
ENRICH_CONCURRENCY = 4
EXCLUDE_FLIGHTS_NOTE = "Cost estimates cover accommodation, food, local transport and activities — not international flights."
_DEFAULT_PERIOD = "Check seasonal patterns for this destination."


@dataclass
class DiscoverPorts:
    llm: Callable[[list[dict[str, str]], float, int], Awaitable[str]]
    geocode: Callable[[str], Awaitable[Any]]                       # -> object with latitude, longitude, country
    forecast: Callable[[float, float, int], Awaitable[list[dict[str, Any]]]]
    image: Callable[[str], Awaitable[Optional[dict[str, str]]]]    # -> {"url", "attribution"} | None
    activities: Callable[[float, float], Awaitable[list[str]]]
    usd_rate: Callable[[str], Awaitable[float]]                    # 1 USD expressed in `currency`
    load_profile: Callable[[], Awaitable[dict[str, Any]]]
    cache_get: Callable[[str], Awaitable[Optional[list[dict[str, Any]]]]]
    persist: Callable[[list[dict[str, Any]], dict[str, Any]], Awaitable[dict[str, Any]]]
    today: Callable[[], date] = date.today


class DiscoverState(TypedDict, total=False):
    request: dict[str, Any]
    constraints: dict[str, Any]
    profile: dict[str, Any]
    notes: list[str]
    cache_key: str
    source: str                          # "cache" | "live"
    rate: float                          # 1 USD in the budget currency
    candidates: list[dict[str, Any]]
    enriched: list[dict[str, Any]]
    results: list[dict[str, Any]]        # scored items (cached, or built by rank_results)
    ranked_full: list[dict[str, Any]]    # the un-personalized ranked list that gets cached
    outcome: dict[str, Any]
    error: dict[str, Any]


def _fail(code: str, message: str, *, retryable: bool = False) -> dict[str, Any]:
    return {"error": {"code": code, "message": message, "retryable": retryable}}


def _as_date(value: Any) -> Optional[date]:
    if value is None or isinstance(value, date):
        return value
    return date.fromisoformat(str(value))


def make_nodes(ports: DiscoverPorts) -> dict[str, Callable[[dict[str, Any]], Awaitable[dict[str, Any]]]]:
    async def prepare_constraints(state: DiscoverState) -> dict[str, Any]:
        try:
            profile = await ports.load_profile()
        except Exception as exc:  # noqa: BLE001 - personalization is optional
            logger.warning("discover_profile_unavailable: %s", exc)
            profile = {}
        constraints, notes = build_constraints(state["request"], profile)
        return {"constraints": constraints, "notes": notes, "profile": profile, "cache_key": cache_key(constraints)}

    async def cache_lookup(state: DiscoverState) -> dict[str, Any]:
        try:
            cached = await ports.cache_get(state["cache_key"])
        except Exception as exc:  # noqa: BLE001
            logger.warning("discover_cache_unavailable: %s", exc)
            cached = None
        if cached:
            return {"results": cached, "source": "cache"}
        return {"source": "live"}

    async def normalize_currency(state: DiscoverState) -> dict[str, Any]:
        currency = state["constraints"]["budget_currency"]
        if currency == "USD":
            return {"rate": 1.0}
        try:
            rate = float(await ports.usd_rate(currency))
        except Exception as exc:  # noqa: BLE001
            logger.warning("discover_rate_unavailable %s: %s", currency, exc)
            return _fail("currency_unavailable",
                         f"Exchange rates for {currency} are unavailable, so affordability cannot be checked right now.",
                         retryable=True)
        if rate <= 0:
            return _fail("currency_unavailable", "Received an unusable exchange rate.", retryable=True)
        return {"rate": rate}

    async def generate_candidates(state: DiscoverState) -> dict[str, Any]:
        constraints = state["constraints"]
        count = constraints["max_results"] + EXTRA_CANDIDATES
        messages = build_candidate_messages(
            constraints, count=count, memories=state.get("profile", {}).get("memories", []),
            usd_per_unit=1.0 / state["rate"],
        )
        for temperature in (0.4, 0.2):
            try:
                raw = await ports.llm(messages, temperature, 900)
            except ProviderUnavailableError:
                return _fail("ai_unavailable", "The recommendation service is temporarily unavailable.", retryable=True)
            try:
                return {"candidates": parse_candidates(raw, count)}
            except CandidateParseError as exc:
                logger.warning("discover_candidates_rejected: %s", exc)
        return _fail("ai_output_invalid", "Could not generate destination recommendations right now. Please try again.",
                     retryable=True)

    async def enrich_candidates(state: DiscoverState) -> dict[str, Any]:
        constraints = state["constraints"]
        start, end = _as_date(constraints.get("start_date")), _as_date(constraints.get("end_date"))
        today = ports.today()
        wants_forecast = (
            start is not None and start <= today + timedelta(days=FORECAST_HORIZON_DAYS) and (end or start) >= today
        )
        semaphore = asyncio.Semaphore(ENRICH_CONCURRENCY)

        async def enrich(candidate: dict[str, Any]) -> Optional[dict[str, Any]]:
            async with semaphore:
                try:
                    geo = await ports.geocode(candidate["destination"])
                except Exception as exc:  # noqa: BLE001 - unverifiable place: dropped, never given invented coordinates
                    logger.warning("discover_geocode_failed %s: %s", candidate["destination"], exc)
                    return None
                if not country_matches(candidate.get("country_code"), getattr(geo, "country", None)):
                    logger.warning("discover_country_mismatch %s", candidate["destination"])
                    return None
                days = min(FORECAST_HORIZON_DAYS, max(1, ((end or start) - today).days + 1)) if wants_forecast else 0
                forecast, image, activities = await asyncio.gather(
                    ports.forecast(geo.latitude, geo.longitude, days) if wants_forecast else _none_list(),
                    ports.image(candidate["destination"]),
                    ports.activities(geo.latitude, geo.longitude),
                    return_exceptions=True,
                )
            return {
                "candidate": candidate, "geo": geo,
                "forecast": [] if isinstance(forecast, Exception) else forecast,
                "image": None if isinstance(image, Exception) else image,
                "activities": [] if isinstance(activities, Exception) else activities,
            }

        results = await asyncio.gather(*(enrich(c) for c in state["candidates"]))
        return {"enriched": [r for r in results if r is not None]}

    async def rank_results(state: DiscoverState) -> dict[str, Any]:
        constraints = state["constraints"]
        notes = list(state.get("notes", []))
        if state.get("source") == "cache":
            ranked = list(state["results"])
        else:
            ranked = _build_ranked(state)
            notes.append(EXCLUDE_FLIGHTS_NOTE)

        results = ranked
        visited = [v for v in state.get("profile", {}).get("visited", []) if v]
        if visited and constraints.get("exclude_visited", True):
            kept = [r for r in results if not any(same_place(r["destination"], v) for v in visited)]
            left_out = [r["destination"] for r in results if r not in kept]
            if left_out:
                notes.append("Left out places you've already visited: " + ", ".join(left_out[:3]) + ".")
            results = kept
        results = results[: constraints["max_results"]]
        if not results:
            return {**_fail("no_destinations_found",
                            "No destination could be verified for those constraints. Try a higher budget or fewer restrictions."),
                    "notes": notes}
        if state.get("source") == "cache" and EXCLUDE_FLIGHTS_NOTE not in notes:
            notes.append(EXCLUDE_FLIGHTS_NOTE)
        return {"results": results, "ranked_full": ranked, "notes": notes}

    async def persist(state: DiscoverState) -> dict[str, Any]:
        meta = {
            "source": state["source"], "cache_key": state["cache_key"], "notes": state.get("notes", []),
            "cache_payload": state.get("ranked_full", []),
        }
        return {"outcome": await ports.persist(state["results"], meta)}

    async def fail(state: DiscoverState) -> dict[str, Any]:
        return {} if state.get("error") else _fail("discover_failed", "Destination search failed.", retryable=True)

    return {
        "prepare_constraints": prepare_constraints, "cache_lookup": cache_lookup,
        "normalize_currency": normalize_currency, "generate_candidates": generate_candidates,
        "enrich_candidates": enrich_candidates, "rank_results": rank_results, "persist": persist, "fail": fail,
    }


async def _none_list() -> list:
    return []


def _build_ranked(state: DiscoverState) -> list[dict[str, Any]]:
    """Cost, scores and validation for every VERIFIED candidate, best first."""
    constraints, rate = state["constraints"], state["rate"]
    days, travelers = constraints["duration_days"], constraints["travelers"]
    currency, budget = constraints["budget_currency"], constraints["budget_amount"]
    start, end = _as_date(constraints.get("start_date")), _as_date(constraints.get("end_date"))

    items: list[dict[str, Any]] = []
    for entry in state.get("enriched", []):
        candidate, geo = entry["candidate"], entry["geo"]
        total = round(candidate["estimated_daily_cost_usd"] * days * travelers * rate, 2)
        if total <= 0 or not (-90 <= geo.latitude <= 90 and -180 <= geo.longitude <= 180):
            continue                                                       # validation (§11)
        activities = list(entry["activities"])[:5]
        period = candidate.get("best_travel_period")
        season = season_fit(start, end, period)
        score = compute_overall_score(
            budget_fit=compute_budget_fit_score(total, budget),
            interest_match=compute_interest_match_score(
                constraints.get("interests") or [], f"{candidate['reasons']} {' '.join(activities)}"),
            season_fit=season,
        )
        image = entry.get("image") or {}
        items.append({
            "destination": candidate["destination"],
            "country": getattr(geo, "country", None) or candidate.get("country_code"),
            "latitude": geo.latitude, "longitude": geo.longitude,
            "cost_breakdown": build_cost_breakdown(total, currency),
            "suggested_duration_days": days,
            "weather_summary": weather_summary(entry["forecast"], start, end),
            "best_travel_period": period or _DEFAULT_PERIOD,
            "reasons": candidate["reasons"],
            "image_url": image.get("url"), "image_attribution": image.get("attribution"),
            "relevant_activities": activities,
            "over_budget": is_over_budget(total, budget),
            "score": score, "season_fit": season,
            "trip_planning_cta": {
                "destination": candidate["destination"], "origin": constraints.get("origin"),
                "start_date": str(start) if start else None, "end_date": str(end) if end else None,
                "duration_days": days, "travelers": travelers,
                "budget_amount": budget, "budget_currency": currency, "estimated_cost": total,
            },
        })
    items.sort(key=lambda i: i["score"], reverse=True)
    return items


def route_after_cache(state: dict[str, Any]) -> str:
    return "rank_results" if state.get("source") == "cache" else "normalize_currency"


def route_on_error(next_node: str) -> Callable[[dict[str, Any]], str]:
    def route(state: dict[str, Any]) -> str:
        return "fail" if state.get("error") else next_node
    return route


def build_discover_spec(ports: DiscoverPorts) -> WorkflowSpec:
    spec = WorkflowSpec(
        entry="prepare_constraints",
        nodes=make_nodes(ports),
        edges=[("prepare_constraints", "cache_lookup"), ("enrich_candidates", "rank_results")],
        conditionals={
            "cache_lookup": (route_after_cache, {"rank_results": "rank_results", "normalize_currency": "normalize_currency"}),
            "normalize_currency": (route_on_error("generate_candidates"), {"generate_candidates": "generate_candidates", "fail": "fail"}),
            "generate_candidates": (route_on_error("enrich_candidates"), {"enrich_candidates": "enrich_candidates", "fail": "fail"}),
            "rank_results": (route_on_error("persist"), {"persist": "persist", "fail": "fail"}),
        },
        finish=["persist", "fail"],
    )
    spec.validate()
    return spec
