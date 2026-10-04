"""
Prompts and strict parsing for the itinerary model (Master Prompt §81:
"never rely on free-form text for core itinerary data").

The model is asked for ONE JSON object. Its output is treated as untrusted
input: it is parsed defensively, every field is type-checked and clamped, and
nothing reaches the database until the planning rules have accepted it.
Pure stdlib — no framework imports — so it is fully unit-testable.
"""
from __future__ import annotations

import json
import re
from datetime import time
from typing import Any, Optional

from app.modules.planning.domain import ITEM_TYPES, Issue, PlannedDay, PlannedItem, PlannedTrip, TripSpec
from app.modules.planning.rules import expected_dates

MAX_ITEMS_PER_DAY = 15
MAX_TRIP_DAYS = 14

_TIME_RE = re.compile(r"^\s*(\d{1,2}):(\d{2})(?::\d{2})?\s*$")
_CURRENCY_RE = re.compile(r"^[A-Za-z]{3}$")


class PlanParseError(ValueError):
    """The model's output could not be turned into a plan."""


# ---------------------------------------------------------------------------
# Prompts
# ---------------------------------------------------------------------------
_SCHEMA_DESCRIPTION = """\
{
  "overview": "2-4 sentence summary of the trip",
  "days": [
    {
      "day_number": 1,
      "items": [
        {
          "item_type": "hotel | activity | attraction | restaurant | transport | note",
          "title": "short title",
          "description": "1-2 sentences or null",
          "location_name": "exact place name and city, e.g. 'Louvre Museum, Paris'",
          "start_time": "HH:MM (24h) or null",
          "end_time": "HH:MM (24h) or null",
          "estimated_cost": 0.0,
          "external_id": "the id of a VERIFIED option, or null",
          "outdoor": false,
          "must_see": false,
          "notes": "practical tip or null"
        }
      ]
    }
  ]
}"""

_GENERATION_RULES = """\
You are Tour-Wayva's itinerary planner. Reply with ONE JSON object that matches the schema below and NOTHING else (no prose, no markdown).

Hard rules:
1. Plan EXACTLY {num_days} days, numbered 1..{num_days}, for a group of {travelers} traveller(s).
2. Every cost is the TOTAL for the whole group, in {currency}. Use null when you do not know a price — never guess a precise price for a verified option.
3. VERIFIED options (hotels, activities) are listed in the user message with an "id". To use one, copy its id into "external_id" and keep its name; its price will be taken from the verified data, not from you. Anything else you suggest must have "external_id": null and must be a real, well-known place; give its exact name and city in "location_name" so it can be verified.
4. Times are 24h "HH:MM". Leave realistic time to travel between places and to eat. Do not overlap items. At most {max_items} items per day. Put the accommodation as a "hotel" item on day 1.
5. Respect the preferences: include every "must_see" place (set "must_see": true), never include anything in "avoid", match pace, walking level, interests and food preferences.
6. Mark items that are outdoors with "outdoor": true. On days forecast to be wet, prefer indoor plans.
7. Keep the total cost within the budget when one is given.
8. Write titles, descriptions and notes in the language given by "language" in the user message (English when absent).

Schema:
{schema}"""


def build_generation_messages(
    *,
    spec: TripSpec,
    currency: str,
    preferences: dict[str, Any],
    memories: list[str],
    geo: dict[str, Any],
    hotels: list[dict[str, Any]],
    activities: list[dict[str, Any]],
    forecast: list[dict[str, Any]],
    language: str = "en",
) -> list[dict[str, str]]:
    system = _GENERATION_RULES.format(
        num_days=spec.num_days, travelers=spec.travelers, currency=currency,
        max_items=MAX_ITEMS_PER_DAY, schema=_SCHEMA_DESCRIPTION,
    )
    dates = [d.isoformat() for d in expected_dates(spec)]
    context = {
        "language": language,
        "destination": spec.destination,
        "origin": spec.origin,
        "dates": dates,
        "travelers": spec.travelers,
        "budget": {"amount": spec.budget_amount, "currency": spec.budget_currency} if spec.budget_amount else None,
        "preferences": {k: v for k, v in preferences.items() if v},
        "known_about_traveller": memories[:10],
        "destination_location": {"latitude": geo.get("latitude"), "longitude": geo.get("longitude"),
                                 "country": geo.get("country")},
        "verified_hotels": hotels,
        "verified_activities": activities,
        "weather_forecast": forecast,
    }
    return [
        {"role": "system", "content": system},
        {"role": "user", "content": json.dumps(context, ensure_ascii=False, default=str)},
    ]


def build_repair_messages(
    *,
    spec: TripSpec,
    currency: str,
    plan: PlannedTrip,
    issues: list[Issue],
    hotels: list[dict[str, Any]],
    activities: list[dict[str, Any]],
) -> list[dict[str, str]]:
    system = _GENERATION_RULES.format(
        num_days=spec.num_days, travelers=spec.travelers, currency=currency,
        max_items=MAX_ITEMS_PER_DAY, schema=_SCHEMA_DESCRIPTION,
    ) + (
        "\n\nYou are REVISING an existing plan. Fix every listed problem while changing as little as possible, "
        "and return the COMPLETE corrected plan in the same schema."
    )
    payload = {
        "current_plan": plan_to_dict(plan),
        "problems_to_fix": [
            {"code": i.code, "day": i.day_number, "item_index": i.item_index, "problem": i.message}
            for i in issues
        ],
        "budget": {"amount": spec.budget_amount, "currency": spec.budget_currency} if spec.budget_amount else None,
        "verified_hotels": hotels,
        "verified_activities": activities,
    }
    return [
        {"role": "system", "content": system},
        {"role": "user", "content": json.dumps(payload, ensure_ascii=False, default=str)},
    ]


# ---------------------------------------------------------------------------
# Parsing
# ---------------------------------------------------------------------------
def extract_json_object(text: str) -> dict[str, Any]:
    """Pull the JSON object out of a model reply, tolerating ``` fences and
    chatter around it."""
    if not isinstance(text, str) or not text.strip():
        raise PlanParseError("Empty model output.")
    cleaned = re.sub(r"^```(?:json)?\s*|\s*```$", "", text.strip(), flags=re.IGNORECASE)
    start, end = cleaned.find("{"), cleaned.rfind("}")
    if start == -1 or end <= start:
        raise PlanParseError("No JSON object found in model output.")
    try:
        data = json.loads(cleaned[start : end + 1])
    except json.JSONDecodeError as exc:
        raise PlanParseError(f"Model output is not valid JSON: {exc.msg}") from exc
    if not isinstance(data, dict):
        raise PlanParseError("Model output is not a JSON object.")
    return data


def _text(value: Any, limit: int) -> Optional[str]:
    if not isinstance(value, str):
        return None
    value = value.strip()
    return value[:limit] if value else None


def _number(value: Any) -> Optional[float]:
    if isinstance(value, bool) or value is None:
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if number == number and abs(number) != float("inf") else None  # reject NaN/inf


def _time(value: Any) -> Optional[time]:
    if not isinstance(value, str):
        return None
    match = _TIME_RE.match(value)
    if not match:
        return None
    hour, minute = int(match.group(1)), int(match.group(2))
    return time(hour, minute) if hour <= 23 and minute <= 59 else None


def _bool(value: Any) -> bool:
    return value is True


def parse_llm_plan(raw: str, spec: TripSpec, currency: str) -> PlannedTrip:
    data = extract_json_object(raw)
    days_raw = data.get("days")
    if not isinstance(days_raw, list) or not days_raw:
        raise PlanParseError("The plan has no days.")

    def day_key(entry: Any) -> float:
        number = _number(entry.get("day_number")) if isinstance(entry, dict) else None
        return number if number is not None else float("inf")

    ordered = sorted((d for d in days_raw if isinstance(d, dict)), key=day_key)
    dates = expected_dates(spec)
    days: list[PlannedDay] = []
    for index, target_date in enumerate(dates[:MAX_TRIP_DAYS]):
        entry = ordered[index] if index < len(ordered) else None
        items: list[PlannedItem] = []
        if entry is not None:
            for item_raw in (entry.get("items") or [])[:MAX_ITEMS_PER_DAY]:
                item = _parse_item(item_raw, currency)
                if item is not None:
                    items.append(item)
        days.append(PlannedDay(day_number=index + 1, date=target_date, items=items))

    return PlannedTrip(overview=_text(data.get("overview"), 1200) or "", currency=currency, days=days)


def _parse_item(raw: Any, currency: str) -> Optional[PlannedItem]:
    if not isinstance(raw, dict):
        return None
    title = _text(raw.get("title"), 200)
    if not title:
        return None
    item_type = str(raw.get("item_type") or "custom").strip().lower()
    if item_type not in ITEM_TYPES:
        item_type = "custom"

    cost = _number(raw.get("estimated_cost"))
    if cost is not None and cost < 0:
        cost = None
    return PlannedItem(
        item_type=item_type,
        title=title,
        description=_text(raw.get("description"), 2000),
        location_name=_text(raw.get("location_name"), 200),
        start_time=_time(raw.get("start_time")),
        end_time=_time(raw.get("end_time")),
        estimated_cost=round(cost, 2) if cost is not None else None,
        currency=currency if cost is not None else None,
        source="estimated",
        external_id=_text(raw.get("external_id"), 100),
        notes=_text(raw.get("notes"), 1000),
        outdoor=_bool(raw.get("outdoor")),
        must_see=_bool(raw.get("must_see")),
    )


# ---------------------------------------------------------------------------
# Serialization
# ---------------------------------------------------------------------------
def item_to_dict(item: PlannedItem) -> dict[str, Any]:
    return {
        "item_type": item.item_type,
        "title": item.title,
        "description": item.description,
        "location_name": item.location_name,
        "latitude": item.latitude,
        "longitude": item.longitude,
        "start_time": item.start_time.strftime("%H:%M") if item.start_time else None,
        "end_time": item.end_time.strftime("%H:%M") if item.end_time else None,
        "estimated_cost": item.estimated_cost,
        "currency": item.currency,
        "provider": item.provider,
        "source": item.source,
        "external_id": item.external_id,
        "booking_link": item.booking_link,
        "image_url": item.image_url,
        "notes": item.notes,
        "outdoor": item.outdoor,
        "must_see": item.must_see,
    }


def plan_to_dict(plan: PlannedTrip) -> dict[str, Any]:
    return {
        "overview": plan.overview,
        "currency": plan.currency,
        "days": [
            {
                "day_number": d.day_number,
                "date": d.date.isoformat(),
                "weather_summary": d.weather_summary,
                "items": [item_to_dict(i) for i in d.items],
            }
            for d in plan.days
        ],
    }


def _iso_date(value: Any):
    from datetime import date as date_cls

    if not isinstance(value, str):
        raise PlanParseError("A day is missing its date.")
    try:
        return date_cls.fromisoformat(value)
    except ValueError as exc:
        raise PlanParseError("A day has an invalid date.") from exc


def plan_from_dict(data: dict[str, Any]) -> PlannedTrip:
    """Inverse of plan_to_dict — used to apply a stored (already validated)
    proposal. Still parsed defensively: it came out of a JSON column."""
    if not isinstance(data, dict) or not isinstance(data.get("days"), list):
        raise PlanParseError("Stored plan is malformed.")
    currency = str(data.get("currency") or "USD").upper()[:3]
    days: list[PlannedDay] = []
    for entry in data["days"]:
        items: list[PlannedItem] = []
        for raw in entry.get("items") or []:
            title = _text(raw.get("title"), 200) if isinstance(raw, dict) else None
            if not title:
                continue
            item_type = str(raw.get("item_type") or "custom").lower()
            cost = _number(raw.get("estimated_cost"))
            items.append(PlannedItem(
                item_type=item_type if item_type in ITEM_TYPES else "custom",
                title=title,
                description=_text(raw.get("description"), 2000),
                location_name=_text(raw.get("location_name"), 200),
                latitude=_number(raw.get("latitude")),
                longitude=_number(raw.get("longitude")),
                start_time=_time(raw.get("start_time")),
                end_time=_time(raw.get("end_time")),
                estimated_cost=round(cost, 2) if cost is not None and cost >= 0 else None,
                currency=_text(raw.get("currency"), 3),
                provider=_text(raw.get("provider"), 50),
                source="provider" if raw.get("source") == "provider" else "estimated",
                external_id=_text(raw.get("external_id"), 100),
                booking_link=_text(raw.get("booking_link"), 500),
                image_url=_text(raw.get("image_url"), 500),
                notes=_text(raw.get("notes"), 1000),
                outdoor=_bool(raw.get("outdoor")),
                must_see=_bool(raw.get("must_see")),
            ))
        days.append(PlannedDay(
            day_number=int(entry.get("day_number") or len(days) + 1), date=_iso_date(entry.get("date")),
            items=items, weather_summary=_text(entry.get("weather_summary"), 255),
        ))
    return PlannedTrip(overview=_text(data.get("overview"), 1200) or "", currency=currency, days=days)


_REVISION_RULES = (
    "\n\nYou are REVISING an existing itinerary because the traveller asked for a change. Apply their request "
    "with the SMALLEST possible change and keep everything unrelated exactly as it is (same titles, times and "
    "external_id). Items that have an external_id are VERIFIED provider items: keep them unchanged or remove them if "
    "the request requires it — never edit their price or details. Anything you add must have external_id null and "
    "is an estimate. Always keep the SAME number of days and dates. If asked to remove a day, remove everything "
    "planned on it (the day becomes free); the trip's dates are not changed by a revision. Return the COMPLETE "
    "revised plan in the same schema. If the request cannot be applied, return the plan unchanged."
)


def build_revision_messages(
    *, spec: TripSpec, currency: str, plan: PlannedTrip, instruction: str, language: str = "en"
) -> list[dict[str, str]]:
    system = _GENERATION_RULES.format(
        num_days=spec.num_days, travelers=spec.travelers, currency=currency,
        max_items=MAX_ITEMS_PER_DAY, schema=_SCHEMA_DESCRIPTION,
    ) + _REVISION_RULES
    payload = {
        "traveller_request": instruction,
        "language": language,
        "current_plan": plan_to_dict(plan),
        "budget": {"amount": spec.budget_amount, "currency": spec.budget_currency} if spec.budget_amount else None,
    }
    return [
        {"role": "system", "content": system},
        {"role": "user", "content": json.dumps(payload, ensure_ascii=False, default=str)},
    ]


def weather_summary(entry: dict[str, Any]) -> str:
    parts = [str(entry.get("condition") or "").strip()]
    high, low = entry.get("high_c"), entry.get("low_c")
    if isinstance(high, (int, float)) and isinstance(low, (int, float)):
        parts.append(f"{round(low)}-{round(high)}°C")
    chance = entry.get("chance_of_rain_pct")
    if isinstance(chance, (int, float)):
        parts.append(f"{round(chance)}% chance of rain")
    return ", ".join(p for p in parts if p)[:255]
