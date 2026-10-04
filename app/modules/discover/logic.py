"""
Pure Discover logic (Master Prompt §10-12) — no framework imports, so every
rule is unit-tested without a database or network.

What lives here: how the effective search constraints are built (request first,
saved profile second), the cache key, strict parsing of the model's candidate
list, "season fit" (does the trip fall in the destination's best period?), and
the weather summary for the traveller's actual dates.
"""
from __future__ import annotations

import hashlib
import json
import math
import re
from datetime import date, timedelta
from typing import Any, Optional

MAX_DAILY_COST_USD = 3000.0
MIN_DAILY_COST_USD = 5.0
EXTRA_CANDIDATES = 2          # over-generate: some will fail verification or were already visited
CACHE_VERSION = "v2"


class CandidateParseError(ValueError):
    """The model's destination list could not be used."""


# ---------------------------------------------------------------------------
# Constraints
# ---------------------------------------------------------------------------
_PROFILE_FALLBACKS = (
    # (constraint name, profile key, label shown to the user)
    ("interests", "interests", "saved interests"),
    ("travel_style", "travel_style", "saved travel style"),
    ("accommodation_preference", "accommodation_preference", "saved accommodation preference"),
    ("transportation_preference", "transportation_preference", "saved transport preference"),
)


def build_constraints(request: dict[str, Any], profile: dict[str, Any]) -> tuple[dict[str, Any], list[str]]:
    """Effective constraints. The request always wins; the user's saved
    profile only fills fields the request left empty, and only what the user
    voluntarily provided is used. Returns (constraints, personalization notes)."""
    constraints = dict(request)
    notes: list[str] = []
    for name, key, label in _PROFILE_FALLBACKS:
        if constraints.get(name) in (None, "", []) and profile.get(key):
            constraints[name] = profile[key]
            notes.append(f"Used your {label}.")
    constraints["interests"] = sorted({str(i).strip().lower() for i in constraints.get("interests") or [] if str(i).strip()})
    constraints["budget_amount"] = float(constraints["budget_amount"])
    constraints["budget_currency"] = str(constraints["budget_currency"]).upper()
    return constraints, notes


def cache_key(constraints: dict[str, Any]) -> str:
    """Deterministic key over the constraints that shape the RANKED result.
    User-specific things (visited places) are NOT in it — they are applied
    after the cache, so one user's history never leaks into another's results.
    The trip start date is included: season changes the right answer."""
    canonical = {
        "budget": round(constraints["budget_amount"]),
        "currency": constraints["budget_currency"],
        "origin": (constraints.get("origin") or "").strip().lower(),
        "continent": (constraints.get("continent") or "").strip().lower(),
        "region": (constraints.get("country_region") or "").strip().lower(),
        "start": str(constraints.get("start_date") or ""),
        "days": constraints["duration_days"],
        "travelers": constraints["travelers"],
        "style": (constraints.get("travel_style") or "").strip().lower(),
        "interests": constraints.get("interests") or [],
        "climate": (constraints.get("climate_preference") or "").strip().lower(),
        "accommodation": (constraints.get("accommodation_preference") or "").strip().lower(),
        "transport": (constraints.get("transportation_preference") or "").strip().lower(),
        "n": constraints["max_results"],
    }
    digest = hashlib.sha256(json.dumps(canonical, sort_keys=True).encode()).hexdigest()[:32]
    return f"discover:{CACHE_VERSION}:{digest}"


# ---------------------------------------------------------------------------
# Prompt
# ---------------------------------------------------------------------------
_CANDIDATE_PROMPT = (
    "You are a travel destination recommendation engine. Propose {count} real, specific destinations "
    "(city + country) that genuinely fit the traveller's constraints. The budget is the TOTAL for the whole group "
    "of {travelers} traveller(s) for {days} day(s). For each destination estimate a realistic average cost PER PERSON "
    "PER DAY in USD covering accommodation, food, local transport and activities — NOT international flights. "
    "Be realistic, not aspirational, and only suggest destinations whose realistic cost can fit the budget. "
    "Respond with ONLY a JSON array, no other text:\n\n"
    '[{{"destination": "City", "country_code": "ISO 3166-1 alpha-2", "reasons": "1-2 sentences on why this fits", '
    '"estimated_daily_cost_usd": number, "best_travel_period": "e.g. \'April to June\'"}}]'
)


def build_candidate_messages(
    constraints: dict[str, Any], *, count: int, memories: list[str], usd_per_unit: Optional[float]
) -> list[dict[str, str]]:
    days, travelers = constraints["duration_days"], constraints["travelers"]
    per_person_day = constraints["budget_amount"] / travelers / days
    facts: dict[str, Any] = {
        "total_budget": f"{constraints['budget_amount']:g} {constraints['budget_currency']}",
        "budget_per_person_per_day": f"{per_person_day:.2f} {constraints['budget_currency']}",
    }
    if usd_per_unit:
        facts["budget_per_person_per_day_usd"] = round(per_person_day * usd_per_unit, 2)
    for key in ("origin", "continent", "country_region", "start_date", "end_date", "travel_style",
                "climate_preference", "accommodation_preference", "transportation_preference"):
        if constraints.get(key):
            facts[key] = str(constraints[key])
    if constraints.get("interests"):
        facts["interests"] = constraints["interests"]
    if memories:
        facts["known_about_traveller"] = memories[:5]
    return [
        {"role": "system", "content": _CANDIDATE_PROMPT.format(count=count, travelers=travelers, days=days)},
        {"role": "user", "content": json.dumps(facts, ensure_ascii=False, default=str)},
    ]


# ---------------------------------------------------------------------------
# Parsing the model's list
# ---------------------------------------------------------------------------
def extract_json_array(text: str) -> list[Any]:
    if not isinstance(text, str) or not text.strip():
        raise CandidateParseError("Empty model output.")
    cleaned = re.sub(r"^```(?:json)?\s*|\s*```$", "", text.strip(), flags=re.IGNORECASE)
    start, end = cleaned.find("["), cleaned.rfind("]")
    if start == -1 or end <= start:
        raise CandidateParseError("No JSON array found.")
    try:
        data = json.loads(cleaned[start : end + 1])
    except json.JSONDecodeError as exc:
        raise CandidateParseError(f"Invalid JSON: {exc.msg}") from exc
    if not isinstance(data, list):
        raise CandidateParseError("Expected a JSON array.")
    return data


def _cost(value: Any) -> Optional[float]:
    if isinstance(value, bool) or value is None:
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if math.isnan(number) or math.isinf(number):
        return None
    return number if MIN_DAILY_COST_USD <= number <= MAX_DAILY_COST_USD else None


def parse_candidates(raw: str, limit: int) -> list[dict[str, Any]]:
    """Sanitized candidates. Anything without a REALISTIC positive cost estimate
    is dropped: a missing/zero cost would otherwise read as "free" and win the
    budget ranking — a fabricated bargain."""
    candidates: list[dict[str, Any]] = []
    seen: set[str] = set()
    for entry in extract_json_array(raw):
        if not isinstance(entry, dict):
            continue
        destination = entry.get("destination")
        cost = _cost(entry.get("estimated_daily_cost_usd"))
        if not isinstance(destination, str) or not destination.strip() or cost is None:
            continue
        destination = destination.strip()[:100]
        key = destination.lower()
        if key in seen:
            continue
        seen.add(key)
        code = str(entry.get("country_code") or "").strip().upper()
        candidates.append({
            "destination": destination,
            "country_code": code if re.fullmatch(r"[A-Z]{2}", code) else None,
            "reasons": str(entry.get("reasons") or "").strip()[:400],
            "estimated_daily_cost_usd": round(cost, 2),
            "best_travel_period": str(entry.get("best_travel_period") or "").strip()[:100] or None,
        })
    if not candidates:
        raise CandidateParseError("No usable destinations in the model output.")
    return candidates[:limit]


def country_matches(claimed: Optional[str], geocoded: Optional[str]) -> bool:
    """A model that says "Georgetown, GY" while geocoding lands in Malaysia has
    named the wrong place. When either side is unknown we cannot tell — accept."""
    if not claimed or not geocoded:
        return True
    return claimed.strip().upper() == geocoded.strip().upper()


def _city(place: str) -> str:
    return place.split(",")[0].strip().lower()


def same_place(a: str, b: str) -> bool:
    """Loose match for "already visited": 'Paris' ~ 'Paris, France' (compares the city part)."""
    city_a, city_b = _city(a), _city(b)
    return bool(city_a) and city_a == city_b


# ---------------------------------------------------------------------------
# Season fit
# ---------------------------------------------------------------------------
_MONTHS = {
    "january": 1, "jan": 1, "february": 2, "feb": 2, "march": 3, "mar": 3, "april": 4, "apr": 4, "may": 5,
    "june": 6, "jun": 6, "july": 7, "jul": 7, "august": 8, "aug": 8, "september": 9, "sept": 9, "sep": 9,
    "october": 10, "oct": 10, "november": 11, "nov": 11, "december": 12, "dec": 12,
}
_MONTH_TOKEN = re.compile(r"\b(" + "|".join(sorted(_MONTHS, key=len, reverse=True)) + r")\b", re.IGNORECASE)
_RANGE_WORDS = re.compile(r"\b(to|through|thru|until)\b|[-–—]", re.IGNORECASE)


def best_months(text: Optional[str]) -> Optional[set[int]]:
    """Months named in a best-travel-period phrase: 'April to June' -> {4,5,6},
    'Nov-Mar' -> {11,12,1,2,3}, 'May and September' -> {5,9}. None when the text
    names no month (e.g. 'year-round') — unknown, not a penalty."""
    if not text:
        return None
    tokens = [(m.start(), _MONTHS[m.group(1).lower()]) for m in _MONTH_TOKEN.finditer(text)]
    if not tokens:
        return None
    if len(tokens) >= 2 and _RANGE_WORDS.search(text[tokens[0][0] : tokens[-1][0]]):
        first, last = tokens[0][1], tokens[-1][1]
        months, current = {first}, first
        while current != last:
            current = current % 12 + 1
            months.add(current)
        return months
    return {month for _, month in tokens}


def trip_months(start: Optional[date], end: Optional[date]) -> Optional[set[int]]:
    if start is None:
        return None
    end = min(max(end or start, start), start + timedelta(days=365))
    months: set[int] = set()
    day = start
    while day <= end:
        months.add(day.month)
        day += timedelta(days=1)
    return months


def season_fit(start: Optional[date], end: Optional[date], best_period: Optional[str]) -> Optional[float]:
    """Share of the trip's months that fall in the destination's best months
    (1.0 = fully in season). None when either side is unknown."""
    wanted, trip = best_months(best_period), trip_months(start, end)
    if not wanted or not trip:
        return None
    return round(len(trip & wanted) / len(trip), 2)


# ---------------------------------------------------------------------------
# Weather for the traveller's actual dates
# ---------------------------------------------------------------------------
def weather_summary(forecast_days: list[dict[str, Any]], start: Optional[date], end: Optional[date]) -> Optional[str]:
    """Only forecast days that fall inside the trip dates count; without dates,
    or when the trip is beyond the forecast horizon, there is nothing honest to
    say (a "next 10 days" forecast would describe the wrong week)."""
    if start is None:
        return None
    end = end or start
    inside = [d for d in forecast_days if start.isoformat() <= str(d.get("date")) <= end.isoformat()]
    highs = [d["high_c"] for d in inside if isinstance(d.get("high_c"), (int, float))]
    lows = [d["low_c"] for d in inside if isinstance(d.get("low_c"), (int, float))]
    if not highs or not lows:
        return None
    return f"Forecast {min(lows):.0f}-{max(highs):.0f}\u00b0C for {len(inside)} of your trip days"
