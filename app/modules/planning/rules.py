"""
Itinerary validation and deterministic repair (Master Prompt §16, §26).

Pure functions over the dataclasses in domain.py — no I/O — so every rule is
unit-tested and the same rules judge AI output, repaired output, and (later)
user edits.

Severity
  error    the plan is not acceptable as-is (must be repaired or rejected)
  warning  worth telling the user; never blocks saving

The generation workflow saves a plan only when no *error* remains
("Never blindly save invalid AI output", §16).
"""
from __future__ import annotations

import math
import re
from dataclasses import dataclass
from datetime import date, time, timedelta
from typing import Optional

from app.modules.planning.domain import OPTIONAL_COST_TYPES, Issue, PlannedDay, PlannedItem, PlannedTrip, TripSpec
from app.utils.opening_hours import WEEKDAY_NAMES, parse_opening_hours

_RAIN_WORDS = re.compile(r"rain|storm|thunder|shower|drizzle|snow", re.IGNORECASE)


@dataclass(frozen=True)
class RuleConfig:
    max_timed_items_per_day: int = 8
    max_distance_from_destination_km: float = 150.0
    travel_speed_kmh: float = 35.0          # realistic city average incl. traffic/transfers
    travel_buffer_minutes: int = 5
    min_gap_ratio: float = 0.8              # tolerate 20% optimism in the model's timings
    rain_probability_threshold: int = 70    # % — below this the forecast is not treated as a conflict
    min_travel_distance_km: float = 0.3     # closer than this needs no transfer time
    closing_overrun_tolerance_minutes: int = 15   # a visit may run this far past closing before we flag it


DEFAULT_CONFIG = RuleConfig()


# ---------------------------------------------------------------------------
# Small helpers
# ---------------------------------------------------------------------------
def haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    radius = 6371.0088
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dphi = p2 - p1
    dlmb = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dlmb / 2) ** 2
    return 2 * radius * math.asin(math.sqrt(a))


def _minutes(t: time) -> int:
    return t.hour * 60 + t.minute


def _from_minutes(m: int) -> time:
    return time(m // 60, m % 60)


def _has_valid_point(item: PlannedItem) -> bool:
    return (
        item.latitude is not None and item.longitude is not None
        and -90 <= item.latitude <= 90 and -180 <= item.longitude <= 180
    )


def travel_minutes(a: PlannedItem, b: PlannedItem, config: RuleConfig = DEFAULT_CONFIG) -> int:
    """Minutes needed to get from `a` to `b` (0 when either has no coordinates
    or they are effectively the same place)."""
    if not (_has_valid_point(a) and _has_valid_point(b)):
        return 0
    distance = haversine_km(a.latitude, a.longitude, b.latitude, b.longitude)
    if distance < config.min_travel_distance_km:
        return 0
    return math.ceil(distance / config.travel_speed_kmh * 60) + config.travel_buffer_minutes


def plan_total_cost(plan: PlannedTrip) -> float:
    return round(sum(i.estimated_cost or 0.0 for d in plan.days for i in d.items), 2)


def sort_day_items(day: PlannedDay) -> None:
    """Timed items first in time order; untimed keep their relative order."""
    indexed = list(enumerate(day.items))
    indexed.sort(key=lambda p: (p[1].start_time is None, p[1].start_time or time.max, p[0]))
    day.items = [item for _, item in indexed]


def _item_key(item: PlannedItem) -> tuple[str, str]:
    return (item.title.strip().lower(), (item.location_name or "").strip().lower())


def expected_dates(spec: TripSpec) -> list[date]:
    return [spec.start_date + timedelta(days=i) for i in range(spec.num_days)]


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------
def validate_plan(
    plan: PlannedTrip,
    spec: TripSpec,
    *,
    forecast: Optional[list[dict]] = None,
    destination_center: Optional[tuple[float, float]] = None,
    config: RuleConfig = DEFAULT_CONFIG,
    allow_empty_days: bool = False,
) -> list[Issue]:
    """`allow_empty_days` is for revisions: "remove Day 3" legitimately leaves a
    free day, so an empty day is only a warning there (for a fresh plan it is
    an error — it almost always means the model skipped a day)."""
    issues: list[Issue] = []

    # -- dates / structure -------------------------------------------------
    expected = expected_dates(spec)
    if len(plan.days) != len(expected):
        issues.append(
            Issue("date_mismatch", "error",
                  f"The trip runs {len(expected)} day(s) but the plan has {len(plan.days)}.")
        )
    else:
        for day, exp in zip(plan.days, expected):
            if day.date != exp:
                issues.append(Issue("date_mismatch", "error",
                                    f"Day {day.day_number} is dated {day.date}, expected {exp}.", day.day_number))

    # -- per-day checks ----------------------------------------------------
    for day in plan.days:
        issues.extend(_validate_day(day, destination_center, config, allow_empty_days))

    # -- cross-day ---------------------------------------------------------
    seen: dict[tuple[str, str], int] = {}
    for day in plan.days:
        for idx, item in enumerate(day.items):
            if item.item_type in ("hotel", "flight", "transport", "note"):
                continue
            key = item.external_id or "|".join(_item_key(item))
            if key in seen and seen[key] != day.day_number:
                issues.append(Issue("repeated_item", "warning",
                                    f"'{item.title}' appears on day {seen[key]} and day {day.day_number}.",
                                    day.day_number, idx))
            seen.setdefault(key, day.day_number)

    # -- budget ------------------------------------------------------------
    issues.extend(_validate_budget(plan, spec))

    # -- accommodation -----------------------------------------------------
    if spec.nights >= 1 and not any(i.item_type == "hotel" for d in plan.days for i in d.items):
        issues.append(Issue("no_accommodation", "warning", "No accommodation is planned for this trip."))

    # -- weather (only where the forecast is trustworthy) -----------------
    issues.extend(_validate_weather(plan, forecast or [], config))

    # -- opening hours (only items whose venue hours were found and understood) ----
    issues.extend(_validate_opening_hours(plan, config))
    return issues


def _validate_day(
    day: PlannedDay, center: Optional[tuple[float, float]], config: RuleConfig, allow_empty: bool = False
) -> list[Issue]:
    issues: list[Issue] = []
    items = day.items

    if not items:
        # Usually a day the model skipped; a genuinely free day can be added by the user later.
        issues.append(Issue("empty_day", "warning" if allow_empty else "error",
                            f"Day {day.day_number} has nothing planned.", day.day_number))

    for idx, item in enumerate(items):
        if item.start_time and item.end_time and item.end_time <= item.start_time:
            issues.append(Issue("end_before_start", "error",
                                f"'{item.title}' ends ({item.end_time}) before it starts ({item.start_time}).",
                                day.day_number, idx))

        lat, lon = item.latitude, item.longitude
        if (lat is None) != (lon is None) or (lat is not None and not (-90 <= lat <= 90)) \
                or (lon is not None and not (-180 <= lon <= 180)):
            issues.append(Issue("invalid_coordinates", "error",
                                f"'{item.title}' has invalid coordinates.", day.day_number, idx))
        elif center is not None and lat is not None and lon is not None:
            distance = haversine_km(center[0], center[1], lat, lon)
            if distance > config.max_distance_from_destination_km:
                issues.append(Issue("outlier_location", "error",
                                    f"'{item.title}' is {distance:.0f} km from the destination.",
                                    day.day_number, idx, {"distance_km": round(distance, 1)}))

    # duplicates within the day (keep the first)
    first_seen: dict[tuple[str, str], int] = {}
    for idx, item in enumerate(items):
        key = _item_key(item)
        if key in first_seen:
            issues.append(Issue("duplicate_item", "error",
                                f"'{item.title}' appears more than once on day {day.day_number}.",
                                day.day_number, idx))
        else:
            first_seen[key] = idx

    # time conflicts and travel feasibility, in time order
    timed = sorted(
        ((idx, it) for idx, it in enumerate(items) if it.start_time and it.end_time and it.end_time > it.start_time),
        key=lambda p: p[1].start_time,
    )
    for (_, a), (b_idx, b) in zip(timed, timed[1:]):
        if b.start_time < a.end_time:
            issues.append(Issue("time_conflict", "error",
                                f"'{a.title}' ({a.start_time}-{a.end_time}) overlaps '{b.title}' "
                                f"({b.start_time}-{b.end_time}).", day.day_number, b_idx))
            continue
        needed = travel_minutes(a, b, config)
        gap = _minutes(b.start_time) - _minutes(a.end_time)
        if needed and gap < needed * config.min_gap_ratio:
            issues.append(Issue("impossible_travel", "error",
                                f"Only {gap} min between '{a.title}' and '{b.title}', but the trip needs "
                                f"about {needed} min.", day.day_number, b_idx, {"needed": needed, "gap": gap}))

    timed_count = sum(1 for i in items if i.start_time)
    if timed_count > config.max_timed_items_per_day:
        issues.append(Issue("excessive_daily_activity", "warning",
                            f"{timed_count} scheduled items on day {day.day_number} is a lot "
                            f"(recommended max {config.max_timed_items_per_day}).", day.day_number))
    return issues


def _validate_budget(plan: PlannedTrip, spec: TripSpec) -> list[Issue]:
    issues: list[Issue] = []
    for day in plan.days:
        for idx, item in enumerate(day.items):
            if item.estimated_cost and item.currency and item.currency.upper() != plan.currency.upper():
                issues.append(Issue("currency_mismatch", "error",
                                    f"'{item.title}' is priced in {item.currency}, not {plan.currency}.",
                                    day.day_number, idx))
    if spec.budget_amount:
        total = plan_total_cost(plan)
        if total > spec.budget_amount:
            issues.append(Issue("over_budget", "error",
                                f"The plan costs about {total:,.2f} {plan.currency}, over the "
                                f"{spec.budget_amount:,.2f} budget.", extra={"total": total, "budget": spec.budget_amount}))
    return issues


def rainy_days(forecast: list[dict], threshold: int) -> set[str]:
    days: set[str] = set()
    for entry in forecast:
        chance = entry.get("chance_of_rain_pct")
        condition = str(entry.get("condition") or "")
        if (isinstance(chance, (int, float)) and chance >= threshold) or _RAIN_WORDS.search(condition):
            days.add(str(entry.get("date")))
    return days


def _validate_weather(plan: PlannedTrip, forecast: list[dict], config: RuleConfig) -> list[Issue]:
    """Only days that HAVE a forecast are judged — beyond the provider's
    horizon we say nothing rather than guess (§26: no deterministic claims
    when forecast confidence is low)."""
    if not forecast:
        return []
    rainy = rainy_days(forecast, config.rain_probability_threshold)
    issues: list[Issue] = []
    for day in plan.days:
        if day.date.isoformat() not in rainy:
            continue
        for idx, item in enumerate(day.items):
            if item.outdoor:
                issues.append(Issue("weather_conflict", "warning",
                                    f"'{item.title}' is outdoors on day {day.day_number}, which is forecast "
                                    "to be wet — consider an indoor alternative or another day.",
                                    day.day_number, idx))
    return issues


def _validate_opening_hours(plan: PlannedTrip, config: RuleConfig) -> list[Issue]:
    """Flags a timed visit that starts while the venue is closed, or that runs
    well past closing. Only items carrying hours the parser fully understood are
    judged — anything else says nothing (unknown is not closed). Always a
    WARNING: the source is community-maintained OpenStreetMap data, so the user is
    told what it says and where it comes from, never that the venue IS closed."""
    issues: list[Issue] = []
    for day in plan.days:
        weekday = day.date.weekday()
        for idx, item in enumerate(day.items):
            if not item.opening_hours or item.start_time is None:
                continue
            hours = parse_opening_hours(item.opening_hours)
            if hours is None:
                continue
            start = _minutes(item.start_time)
            where = f"day {day.day_number} ({WEEKDAY_NAMES[weekday]})"
            source = "per OpenStreetMap, which can be out of date — worth checking before you go"
            if not hours.is_open(weekday, start):
                usual = hours.describe(weekday)
                detail = "it usually has no opening hours that day" if usual == "closed" else f"its usual hours are {usual}"
                issues.append(Issue(
                    "opening_hours_conflict", "warning",
                    f"'{item.title}' starts at {item.start_time:%H:%M} on {where}, but {detail} ({source}).",
                    day.day_number, idx, extra={"opening_hours": item.opening_hours},
                ))
                continue
            closing = hours.closing_minute(weekday, start)
            if item.end_time is not None and closing is not None:
                end = _minutes(item.end_time)
                if end < start:
                    end += 24 * 60          # a visit that runs past midnight
                if end > closing + config.closing_overrun_tolerance_minutes:
                    issues.append(Issue(
                        "opening_hours_conflict", "warning",
                        f"'{item.title}' is planned until {item.end_time:%H:%M} on {where}, but it usually closes at "
                        f"{_fmt_minutes(closing)} ({source}).",
                        day.day_number, idx, extra={"opening_hours": item.opening_hours},
                    ))
    return issues


def _fmt_minutes(total: int) -> str:
    if total > 24 * 60:                     # a closing time past midnight, e.g. 26:00 -> 02:00
        total -= 24 * 60
    return f"{total // 60:02d}:{total % 60:02d}"


# ---------------------------------------------------------------------------
# Deterministic repair
# ---------------------------------------------------------------------------
def repair_plan(
    plan: PlannedTrip,
    spec: TripSpec,
    issues: list[Issue],
    *,
    config: RuleConfig = DEFAULT_CONFIG,
) -> list[str]:
    """Fix what can be fixed without asking the model again. Mutates `plan`
    and returns human-readable descriptions of every change (they are shown
    to the user). Problems that need real re-planning (wrong number of days,
    a budget the fixed costs already exceed) are left for the model."""
    codes = {i.code for i in issues if i.is_error}
    changes: list[str] = []

    if "duplicate_item" in codes:
        for day in plan.days:
            seen: set[tuple[str, str]] = set()
            kept: list[PlannedItem] = []
            for item in day.items:
                key = _item_key(item)
                if key in seen:
                    changes.append(f"Removed the duplicate '{item.title}' on day {day.day_number}.")
                    continue
                seen.add(key)
                kept.append(item)
            day.items = kept

    if codes & {"invalid_coordinates", "outlier_location"}:
        bad = {(i.day_number, i.item_index) for i in issues if i.code in ("invalid_coordinates", "outlier_location")}
        for day in plan.days:
            for idx, item in enumerate(day.items):
                if (day.day_number, idx) in bad:
                    item.latitude = item.longitude = None
                    changes.append(f"Cleared unverified coordinates for '{item.title}'.")

    if codes & {"end_before_start", "time_conflict", "impossible_travel"}:
        for day in plan.days:
            changes.extend(_repair_day_times(day, config))

    if "over_budget" in codes and spec.budget_amount:
        changes.extend(_trim_to_budget(plan, spec.budget_amount))

    for day in plan.days:
        sort_day_items(day)
    return changes


def _repair_day_times(day: PlannedDay, config: RuleConfig) -> list[str]:
    changes: list[str] = []
    timed = sorted((i for i in day.items if i.start_time), key=lambda i: i.start_time)
    previous: Optional[PlannedItem] = None

    for item in timed:
        start = _minutes(item.start_time)
        if item.end_time is None or _minutes(item.end_time) <= start:
            end = min(start + 60, 23 * 60 + 59)
            item.end_time = _from_minutes(end)
            changes.append(f"Set a 1-hour duration for '{item.title}' on day {day.day_number}.")
        duration = _minutes(item.end_time) - start

        if previous is not None and previous.end_time is not None:
            earliest = _minutes(previous.end_time) + travel_minutes(previous, item, config)
            if start < earliest:
                if earliest + duration <= 23 * 60 + 59:
                    item.start_time = _from_minutes(earliest)
                    item.end_time = _from_minutes(earliest + duration)
                    changes.append(f"Moved '{item.title}' to {item.start_time:%H:%M} on day {day.day_number} "
                                   "so it no longer clashes with the previous stop.")
                else:
                    item.start_time = item.end_time = None
                    changes.append(f"Made '{item.title}' flexible on day {day.day_number} (no room left in the day).")
                    continue
        previous = item
    return changes


def _trim_to_budget(plan: PlannedTrip, budget: float) -> list[str]:
    """Drop the most expensive OPTIONAL paid items (never must-see, never
    accommodation/transport) until the total fits the budget."""
    changes: list[str] = []
    while plan_total_cost(plan) > budget:
        # Never empty a day just to save money: that trades one problem for another.
        candidates = [
            (day, item) for day in plan.days if len(day.items) > 1 for item in day.items
            if item.item_type in OPTIONAL_COST_TYPES and not item.must_see and (item.estimated_cost or 0) > 0
        ]
        if not candidates:
            break
        day, item = max(candidates, key=lambda p: p[1].estimated_cost or 0)
        day.items.remove(item)
        changes.append(f"Removed '{item.title}' ({item.estimated_cost:,.2f} {plan.currency}) "
                       f"on day {day.day_number} to fit the budget.")
    return changes


# ---------------------------------------------------------------------------
# Decision helpers used by the workflow
# ---------------------------------------------------------------------------
def blocking_errors(issues: list[Issue]) -> list[Issue]:
    return [i for i in issues if i.is_error]


# Warnings worth ONE round of asking the model to fix them (weather makes an outdoor plan silly; a
# visit is scheduled while the venue looks closed) — never looped on, and never blocking.
_MODEL_REPAIRABLE_WARNINGS = frozenset({"weather_conflict", "opening_hours_conflict"})


def needs_model_repair(issues: list[Issue], attempt: int) -> bool:
    """Ask the model again when errors remain, or (first round only) when a
    warning like a weather or opening-hours conflict is worth one attempt at
    improving — we try that once but never loop on it."""
    if blocking_errors(issues):
        return True
    return attempt == 0 and any(i.code in _MODEL_REPAIRABLE_WARNINGS for i in issues)
