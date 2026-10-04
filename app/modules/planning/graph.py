"""
The itinerary-generation workflow (Master Prompt §14) as a LangGraph graph.

    geocode_destination ─┬─► gather_data ─► normalize_currency ─► draft_itinerary ─┬─► ground_items ─► validate ─┬─► persist
                         └─► fail                                                  └─► fail          ▲          ├─► repair ─┐
                                                                                                        └──────────┘          │
                                                                                                    (repair loops back through ground_items)
                                                                                                                  └─► fail

Principles enforced here
  * AI proposes, systems verify (§P2): hotels/activities/weather/exchange rates
    come from providers; a provider-sourced item's name, price and coordinates
    are taken from provider data, never from the model; the model's own
    suggestions are marked "estimated" and their locations are verified by
    geocoding before coordinates are kept.
  * If a provider is unavailable the plan says so (data_sources) — nothing is
    invented in its place.
  * A plan is persisted only when validation finds no error (§16); otherwise
    the graph fails without touching the trip.

Nodes depend only on `PlanningPorts` (async callables), so the workflow is
testable without a database, network or LangGraph.
"""
from __future__ import annotations

import asyncio
import logging
import math
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Any, Awaitable, Callable, Optional, TypedDict

from app.core.exceptions import ProviderUnavailableError
from app.modules.planning.domain import GeoPoint, PlannedItem, PlannedTrip, TripSpec
from app.modules.planning.llm_io import (
    PlanParseError,
    build_generation_messages,
    build_repair_messages,
    parse_llm_plan,
    weather_summary,
)
from app.modules.planning.rules import (
    blocking_errors,
    haversine_km,
    needs_model_repair,
    repair_plan,
    sort_day_items,
    validate_plan,
)
from app.modules.planning.workflow import WorkflowSpec

logger = logging.getLogger(__name__)

MAX_REPAIR_ATTEMPTS = 2
MAX_GEOCODES_PER_RUN = 40
GEOCODE_CONCURRENCY = 5
OPENING_HOURS_CONCURRENCY = 4
async def _no_city_code() -> Optional[str]:
    return None


async def _no_image() -> Optional[dict[str, str]]:
    return None


async def _no_opening_hours() -> Optional[str]:
    return None


# Item types whose visiting hours matter and are worth a lookup.
_HOURS_TYPES = ("activity", "attraction", "restaurant")
MAX_HOTELS_FOR_MODEL = 8
MAX_HOTEL_IMAGES = 3   # bound the extra image-search calls per generation
MAX_ACTIVITIES_FOR_MODEL = 25
FORECAST_HORIZON_DAYS = 10
_LOCATABLE_TYPES = ("hotel", "activity", "attraction", "restaurant")


@dataclass
class PlanningPorts:
    geocode: Callable[[str], Awaitable[GeoPoint]]
    search_hotels: Callable[[str, date, date, int], Awaitable[list[dict[str, Any]]]]
    search_activities: Callable[[float, float], Awaitable[list[dict[str, Any]]]]
    forecast: Callable[[float, float, int], Awaitable[list[dict[str, Any]]]]
    convert_rate: Callable[[str, str], Awaitable[float]]          # 1 unit of `base` in `target`
    llm: Callable[[list[dict[str, str]], float, int], Awaitable[str]]
    persist: Callable[[PlannedTrip, dict[str, Any]], Awaitable[dict[str, Any]]]
    today: Callable[[], date] = date.today
    # Auto-resolves a destination name to an IATA city code (Master Prompt §21) when the
    # client did not supply one, so hotels are no longer silently skipped for that reason.
    # Default: no-op (always None) — behaviour is unchanged for any caller that omits it.
    resolve_city_code: Callable[[str], Awaitable[Optional[str]]] = lambda destination: _no_city_code()
    # Fetches a small image for a hotel name (Discover-style enrichment). Default: no-op.
    image: Callable[[str], Awaitable[Optional[dict[str, str]]]] = lambda name: _no_image()
    # Opening hours (raw OSM `opening_hours` string) for a venue: (name, latitude, longitude) -> str | None.
    # None means unknown — never "closed". Default: no-op, so the check is simply off for any caller that
    # omits it (revisions included unless they opt in). Must not raise; failures are treated as unknown.
    opening_hours: Callable[[str, float, float], Awaitable[Optional[str]]] = lambda name, lat, lon: _no_opening_hours()
    opening_hours_enabled: bool = False
    max_opening_hours_lookups: int = 15
    opening_hours_timeout_seconds: float = 20.0


class PlanningState(TypedDict, total=False):
    # inputs
    spec: TripSpec
    currency: str
    language: str
    preferences: dict[str, Any]
    memories: list[str]
    options: dict[str, Any]          # {"city_code": "PAR", "include_hotels": bool, "include_activities": bool}
    # gathered
    geo: GeoPoint
    catalog: dict[str, dict[str, Any]]        # short id -> full provider record (hotels "h1", activities "a1")
    raw_hotels: list[dict[str, Any]]          # provider results, before conversion
    raw_activities: list[dict[str, Any]]
    hotels_for_model: list[dict[str, Any]]
    activities_for_model: list[dict[str, Any]]
    forecast: list[dict[str, Any]]
    data_sources: dict[str, str]              # hotels/activities/weather/currency/opening_hours -> ok | unavailable | skipped
    # revision-only inputs
    base_plan: PlannedTrip
    instruction: str
    allow_empty_days: bool
    # working
    plan: PlannedTrip
    issues: list
    repair_attempts: int
    repairs: list[str]
    notes: list[str]
    # outcome
    error: dict[str, Any]
    result: dict[str, Any]


def _fail(code: str, message: str, *, retryable: bool = False) -> dict[str, Any]:
    return {"error": {"code": code, "message": message, "retryable": retryable}}


def _center(state: PlanningState) -> Optional[tuple[float, float]]:
    geo = state.get("geo")
    return (geo.latitude, geo.longitude) if geo else None


# ---------------------------------------------------------------------------
# Nodes
# ---------------------------------------------------------------------------
def make_nodes(ports: PlanningPorts) -> dict[str, Callable[[dict[str, Any]], Awaitable[dict[str, Any]]]]:
    async def geocode_destination(state: PlanningState) -> dict[str, Any]:
        spec = state["spec"]
        if spec.num_days > 14:
            return _fail("trip_too_long", "Itinerary generation supports trips of up to 14 days.")
        try:
            geo = await ports.geocode(spec.destination)
        except ProviderUnavailableError:
            return _fail("geocoding_unavailable", "Location lookup is temporarily unavailable.", retryable=True)
        except Exception:  # noqa: BLE001 - not found / provider error: cannot plan without a verified place
            return _fail("destination_not_found", f"Could not find '{spec.destination}'. Check the spelling.")
        return {"geo": geo}

    async def gather_data(state: PlanningState) -> dict[str, Any]:
        spec, geo, options = state["spec"], state["geo"], state.get("options", {})
        today = ports.today()
        city_code = (options.get("city_code") or "").strip().upper()
        if not city_code:
            try:
                city_code = (await ports.resolve_city_code(spec.destination) or "").upper()
            except Exception as exc:  # noqa: BLE001 - hotels are simply skipped if this fails, like today
                logger.warning("planning_city_code_resolution_failed: %s", exc)

        wanted = {
            "hotels": bool(options.get("include_hotels", True)) and len(city_code) == 3,
            "activities": bool(options.get("include_activities", True)),
            # Outside the forecast horizon we say nothing rather than guess.
            "weather": spec.start_date <= today + timedelta(days=FORECAST_HORIZON_DAYS) and spec.end_date >= today,
        }

        async def hotels() -> list[dict[str, Any]]:
            adults = max(1, min(spec.travelers, 9))
            return await ports.search_hotels(city_code, spec.start_date, spec.end_date, adults)

        async def activities() -> list[dict[str, Any]]:
            return await ports.search_activities(geo.latitude, geo.longitude)

        async def weather() -> list[dict[str, Any]]:
            days = min(FORECAST_HORIZON_DAYS, max(1, (spec.end_date - today).days + 1))
            return await ports.forecast(geo.latitude, geo.longitude, days)

        async def nothing() -> list[dict[str, Any]]:
            return []

        calls = {"hotels": hotels, "activities": activities, "weather": weather}
        names = list(calls)
        results = await asyncio.gather(
            *(calls[n]() if wanted[n] else nothing() for n in names), return_exceptions=True
        )

        sources = {"currency": "ok"}
        data: dict[str, list[dict[str, Any]]] = {}
        for name, result in zip(names, results):
            if not wanted[name]:
                sources[name], data[name] = "skipped", []
            elif isinstance(result, Exception):
                logger.warning("planning_%s_unavailable: %s", name, result)
                sources[name], data[name] = "unavailable", []
            else:
                sources[name], data[name] = "ok", result
        return {
            "data_sources": sources,
            "raw_hotels": data["hotels"],
            "raw_activities": data["activities"],
            "forecast": data["weather"],
        }

    async def normalize_currency(state: PlanningState) -> dict[str, Any]:
        """Convert provider prices into the trip currency with a REAL rate.
        If no rate is available the price is dropped (shown as unknown)."""
        target = state["currency"].upper()
        sources = dict(state.get("data_sources", {}))
        rates: dict[str, Optional[float]] = {target: 1.0}

        async def rate_for(currency: str) -> Optional[float]:
            currency = (currency or "").upper()
            if currency in rates:
                return rates[currency]
            try:
                rates[currency] = float(await ports.convert_rate(currency, target))
            except Exception as exc:  # noqa: BLE001
                logger.warning("planning_rate_unavailable %s->%s: %s", currency, target, exc)
                rates[currency] = None
                sources["currency"] = "unavailable"
            return rates[currency]

        catalog: dict[str, dict[str, Any]] = {}
        for_model_hotels: list[dict[str, Any]] = []
        for_model_acts: list[dict[str, Any]] = []
        travelers = state["spec"].travelers

        priced_hotels = []
        for record in state.get("raw_hotels", []):
            rate = await rate_for(record.get("currency", "")) if record.get("price_total") is not None else None
            total = round(float(record["price_total"]) * rate, 2) if rate else None
            priced_hotels.append({**record, "group_cost": total})
        priced_hotels.sort(key=lambda r: (r["group_cost"] is None, r["group_cost"] or 0))

        # Best-effort thumbnail for the top few hotels only (bounded cost/latency); a failed
        # or missing image never blocks planning — the item is simply shown without one.
        for record in priced_hotels[:MAX_HOTEL_IMAGES]:
            try:
                found = await ports.image(record.get("hotel_name") or "")
                if found:
                    record["picture_url"] = found.get("url")
            except Exception as exc:  # noqa: BLE001
                logger.warning("planning_hotel_image_failed: %s", exc)

        for n, record in enumerate(priced_hotels[:MAX_HOTELS_FOR_MODEL], start=1):
            key = f"h{n}"
            catalog[key] = {**record, "kind": "hotel"}
            for_model_hotels.append(
                {"id": key, "name": record.get("hotel_name"), "room": record.get("room_description"),
                 "total_cost_for_stay": record["group_cost"], "currency": target,
                 "latitude": record.get("latitude"), "longitude": record.get("longitude")}
            )

        for n, record in enumerate(state.get("raw_activities", [])[:MAX_ACTIVITIES_FOR_MODEL], start=1):
            key = f"a{n}"
            price = None
            if record.get("price_amount") is not None:
                rate = await rate_for(record.get("currency", ""))
                price = round(float(record["price_amount"]) * rate, 2) if rate else None
            catalog[key] = {**record, "kind": "activity", "group_cost": round(price * travelers, 2) if price is not None else None}
            for_model_acts.append(
                {"id": key, "name": record.get("name"), "description": (record.get("description") or "")[:160] or None,
                 "price_per_person": price, "currency": target,
                 "latitude": record.get("latitude"), "longitude": record.get("longitude")}
            )
        return {
            "catalog": catalog, "hotels_for_model": for_model_hotels, "activities_for_model": for_model_acts,
            "data_sources": sources,
        }

    async def draft_itinerary(state: PlanningState) -> dict[str, Any]:
        spec, currency = state["spec"], state["currency"]
        messages = build_generation_messages(
            spec=spec, currency=currency, preferences=state.get("preferences", {}),
            memories=state.get("memories", []), geo=_geo_dict(state["geo"]),
            hotels=state.get("hotels_for_model", []), activities=state.get("activities_for_model", []),
            forecast=state.get("forecast", []), language=state.get("language", "en"),
        )
        return await _ask_model(messages, spec, currency, temperatures=(0.5, 0.2), notes=state.get("notes", []))

    async def _ask_model(messages, spec, currency, *, temperatures, notes) -> dict[str, Any]:
        last_error = "unknown"
        for temperature in temperatures:
            try:
                raw = await ports.llm(messages, temperature, 4000)
            except ProviderUnavailableError:
                return _fail("ai_unavailable", "The AI planner is temporarily unavailable. Please try again.", retryable=True)
            try:
                return {"plan": parse_llm_plan(raw, spec, currency), "notes": list(notes)}
            except PlanParseError as exc:
                last_error = str(exc)
                logger.warning("planning_model_output_rejected: %s", last_error)
        return _fail("ai_output_invalid", "The AI planner returned an unusable itinerary. Please try again.", retryable=True)

    async def ground_items(state: PlanningState) -> dict[str, Any]:
        """Replace anything the model claims about verified options with the
        verified data; verify the location of everything else."""
        plan, spec, catalog = state["plan"], state["spec"], state.get("catalog", {})
        center, notes = _center(state), list(state.get("notes", []))
        forecast_by_date = {str(e.get("date")): e for e in state.get("forecast", [])}
        used_hotel_ids: set[str] = set()

        to_geocode: list[PlannedItem] = []
        for day in plan.days:
            entry = forecast_by_date.get(day.date.isoformat())
            day.weather_summary = weather_summary(entry) if entry else None
            kept: list[PlannedItem] = []
            for item in day.items:
                record = catalog.get(item.external_id) if item.external_id else None
                if record is not None:
                    if record["kind"] == "hotel":
                        if item.external_id in used_hotel_ids:
                            notes.append(f"Removed a repeated entry for hotel '{item.title}' (it is booked once per stay).")
                            continue
                        used_hotel_ids.add(item.external_id)
                    _apply_provider_record(item, record, spec.travelers, state["currency"])
                    kept.append(item)
                    continue
                if item.external_id:
                    notes.append(f"Ignored an unrecognised option reference on '{item.title}'.")
                item.external_id, item.source, item.provider = None, "estimated", None
                if item.latitude is None and item.location_name and item.item_type in _LOCATABLE_TYPES:
                    to_geocode.append(item)
                kept.append(item)
            day.items = kept

        await _verify_locations(to_geocode[:MAX_GEOCODES_PER_RUN], center)
        for day in plan.days:
            sort_day_items(day)

        update: dict[str, Any] = {"plan": plan, "notes": notes}
        hours_status = await _attach_opening_hours(plan)
        if hours_status is not None:
            update["data_sources"] = {**state.get("data_sources", {}), "opening_hours": hours_status}
        return update

    async def _attach_opening_hours(plan: PlannedTrip) -> Optional[str]:
        """Looks up hours for the timed, located activities/attractions/restaurants (bounded, concurrent, time-boxed)
        and stores them on the items for validation. Returns the data-source status, or None when there was nothing
        eligible to look up. A lookup that fails, times out or finds nothing just leaves the item without hours."""
        if not ports.opening_hours_enabled:
            return None
        pending: dict[tuple[str, float, float], list[PlannedItem]] = {}
        for day in plan.days:
            for item in day.items:
                if (item.item_type in _HOURS_TYPES and item.start_time is not None and item.opening_hours is None
                        and item.latitude is not None and item.longitude is not None):
                    pending.setdefault((item.title, round(item.latitude, 5), round(item.longitude, 5)), []).append(item)
        if not pending:
            return None

        semaphore = asyncio.Semaphore(OPENING_HOURS_CONCURRENCY)
        found = 0

        async def one(key: tuple[str, float, float], items: list[PlannedItem]) -> None:
            nonlocal found
            title, lat, lon = key
            async with semaphore:
                try:
                    hours = await ports.opening_hours(title, lat, lon)
                except Exception:  # noqa: BLE001 - unknown hours are never a reason to fail planning
                    return
            if hours:
                found += 1
                for item in items:
                    item.opening_hours = hours     # set as each lookup lands, so a timeout keeps what already arrived

        batch = list(pending.items())[: max(ports.max_opening_hours_lookups, 0)]
        try:
            await asyncio.wait_for(
                asyncio.gather(*(one(k, v) for k, v in batch)), timeout=ports.opening_hours_timeout_seconds
            )
        except asyncio.TimeoutError:
            logger.warning("planning_opening_hours_timed_out")
        return "ok" if found else "unavailable"

    async def _verify_locations(items: list[PlannedItem], center: Optional[tuple[float, float]]) -> None:
        semaphore = asyncio.Semaphore(GEOCODE_CONCURRENCY)

        async def one(item: PlannedItem) -> None:
            async with semaphore:
                try:
                    point = await ports.geocode(item.location_name or item.title)
                except Exception:  # noqa: BLE001 - unverifiable => no coordinates, never a guess
                    return
            if center is None or haversine_km(center[0], center[1], point.latitude, point.longitude) <= 150:
                item.latitude, item.longitude = point.latitude, point.longitude

        await asyncio.gather(*(one(i) for i in items))

    async def validate(state: PlanningState) -> dict[str, Any]:
        issues = validate_plan(
            state["plan"], state["spec"], forecast=state.get("forecast"), destination_center=_center(state),
            allow_empty_days=state.get("allow_empty_days", False),
        )
        return {"issues": issues}

    async def repair(state: PlanningState) -> dict[str, Any]:
        plan, spec, currency = state["plan"], state["spec"], state["currency"]
        attempt = state.get("repair_attempts", 0)
        repairs = list(state.get("repairs", []))
        issues = list(state.get("issues", []))

        repairs.extend(repair_plan(plan, spec, issues))
        remaining = validate_plan(
            plan, spec, forecast=state.get("forecast"), destination_center=_center(state),
            allow_empty_days=state.get("allow_empty_days", False),
        )

        update: dict[str, Any] = {"repair_attempts": attempt + 1, "repairs": repairs, "plan": plan}
        if needs_model_repair(remaining, attempt):
            outcome = await _ask_model(
                build_repair_messages(
                    spec=spec, currency=currency, plan=plan, issues=[i for i in remaining if i.is_error or i.code in ("weather_conflict", "opening_hours_conflict")],
                    hotels=state.get("hotels_for_model", []), activities=state.get("activities_for_model", []),
                ),
                spec, currency, temperatures=(0.3,), notes=state.get("notes", []),
            )
            if "error" in outcome:
                # Keep the deterministic result; validation decides whether it is good enough.
                logger.warning("planning_model_repair_failed: %s", outcome["error"]["message"])
            else:
                update["plan"] = outcome["plan"]
                repairs.append("Asked the planner to revise the itinerary to fix remaining problems.")
        return update

    async def persist(state: PlanningState) -> dict[str, Any]:
        meta = {
            "warnings": [i.message for i in state.get("issues", []) if not i.is_error],
            "repairs": state.get("repairs", []),
            "notes": state.get("notes", []),
            "data_sources": state.get("data_sources", {}),
            "currency": state["currency"],
        }
        return {"result": await ports.persist(state["plan"], meta)}

    async def fail(state: PlanningState) -> dict[str, Any]:
        if state.get("error"):
            return {}
        problems = "; ".join(i.message for i in blocking_errors(state.get("issues", []))[:3])
        return _fail("plan_invalid", f"Could not produce a valid itinerary: {problems}", retryable=True)

    return {
        "geocode_destination": geocode_destination, "gather_data": gather_data,
        "normalize_currency": normalize_currency, "draft_itinerary": draft_itinerary,
        "ground_items": ground_items, "validate": validate, "repair": repair,
        "persist": persist, "fail": fail,
    }


def _apply_provider_record(item: PlannedItem, record: dict[str, Any], travelers: int, currency: str) -> None:
    """Overwrite everything the model said about a verified option."""
    # Store the provider's own id (not the short "h1"/"a3" key the model saw), so the
    # item stays identifiable across regenerations and revisions.
    item.external_id = record.get("hotel_id") or record.get("activity_id") or item.external_id
    if record["kind"] == "hotel":
        item.item_type = "hotel"
        item.title = record.get("hotel_name") or item.title
        item.location_name = item.title
        item.description = record.get("room_description") or item.description
        item.estimated_cost = record.get("group_cost")
        item.provider = record.get("provider")
        item.booking_link = None
        item.image_url = record.get("picture_url")
    else:
        if item.item_type not in ("activity", "attraction"):
            item.item_type = "activity"
        item.title = record.get("name") or item.title
        item.location_name = item.title
        item.description = record.get("description") or item.description
        item.estimated_cost = record.get("group_cost")
        item.provider = record.get("provider")
        item.booking_link = record.get("booking_link")
        item.image_url = record.get("picture_url")
    item.currency = currency if item.estimated_cost is not None else None
    item.latitude, item.longitude = record.get("latitude"), record.get("longitude")
    item.source = "provider"


def _geo_dict(geo: GeoPoint) -> dict[str, Any]:
    return {"latitude": geo.latitude, "longitude": geo.longitude, "country": geo.country, "city": geo.city}


# ---------------------------------------------------------------------------
# Routing + spec
# ---------------------------------------------------------------------------
def route_after_geocode(state: dict[str, Any]) -> str:
    return "fail" if state.get("error") else "gather_data"


def route_after_draft(state: dict[str, Any]) -> str:
    return "fail" if state.get("error") else "ground_items"


def route_after_validate(state: dict[str, Any]) -> str:
    issues = state.get("issues", [])
    attempt = state.get("repair_attempts", 0)
    errors = blocking_errors(issues)
    if not errors and not needs_model_repair(issues, attempt):
        return "persist"
    if attempt < MAX_REPAIR_ATTEMPTS:
        return "repair"
    return "fail" if errors else "persist"


def build_generation_spec(ports: PlanningPorts) -> WorkflowSpec:
    spec = WorkflowSpec(
        entry="geocode_destination",
        nodes=make_nodes(ports),
        edges=[
            ("gather_data", "normalize_currency"),
            ("normalize_currency", "draft_itinerary"),
            ("ground_items", "validate"),
            ("repair", "ground_items"),
        ],
        conditionals={
            "geocode_destination": (route_after_geocode, {"gather_data": "gather_data", "fail": "fail"}),
            "draft_itinerary": (route_after_draft, {"ground_items": "ground_items", "fail": "fail"}),
            "validate": (route_after_validate, {"persist": "persist", "repair": "repair", "fail": "fail"}),
        },
        finish=["persist", "fail"],
    )
    spec.validate()
    return spec
