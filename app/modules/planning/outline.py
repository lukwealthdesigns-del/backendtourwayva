"""The whole-trip ROUTE for long trips (the cheap first layer of long-trip planning).

Instead of asking the model for every day of a 60-day trip, we ask once for a small outline: which area the traveller is
in on which days, a theme, a share of the budget and a few highlights. Output size barely depends on trip length, the
call uses the cheaper model tier, and the outline is cached and reused for similar trips (it holds no personal data).
Detailed day-by-day plans are then built a few days at a time on top of it (see chunking.py).

Stdlib only, so it is unit-testable without a database or network.
"""
from __future__ import annotations

import hashlib
import json
from typing import Any, Optional

from app.modules.planning.domain import TripSpec
from app.modules.planning.llm_io import extract_json_object

OUTLINE_VERSION = 1
MAX_SEGMENTS = 24
OUTLINE_MAX_TOKENS = 1600
OUTLINE_CACHE_TTL_SECONDS = 7 * 24 * 3600


class OutlineParseError(ValueError):
    """The model's outline could not be turned into a usable route."""


_RULES = """\
You are Tour-Wayva's trip route planner. Reply with ONE JSON object that matches the schema below and NOTHING else.

Plan only the ROUTE of a {num_days}-day trip (not individual activities).
1. "segments" must cover days 1..{num_days} in order with no gaps and no overlaps; each has "start_day" and "end_day".
2. Use at most {max_segments} segments. Stay in one area unless the destination is a country or wide region where moving
   on makes the trip better; then give each stay at least 2 days, ordered so the travelling makes sense.
3. "area" is a real place that can be geocoded, written "City, Country" (or the destination itself for a single stay).
4. "budget_share" values are fractions of the whole budget and add up to 1. Give more to expensive areas and long stays.
5. "highlights": up to 4 well-known places or experiences for that stay. "theme": at most 12 words.
6. Write "overview", "theme" and "highlights" in the language given by "language" in the user message (English if absent).

Schema:
{{"overview": "2-3 sentences about the whole trip",
 "segments": [{{"start_day": 1, "end_day": 5, "area": "Dubai, United Arab Emirates", "theme": "...", "budget_share": 0.4, "highlights": ["..."]}}]}}"""


def build_outline_messages(*, spec: TripSpec, currency: str, preferences: dict[str, Any], memories: list[str],
                           language: str = "en") -> list[dict[str, str]]:
    system = _RULES.format(num_days=spec.num_days, max_segments=MAX_SEGMENTS)
    context = {
        "language": language,
        "destination": spec.destination,
        "origin": spec.origin,
        "start_date": spec.start_date.isoformat(),
        "end_date": spec.end_date.isoformat(),
        "num_days": spec.num_days,
        "travelers": spec.travelers,
        "budget": {"amount": spec.budget_amount, "currency": spec.budget_currency or currency} if spec.budget_amount else None,
        "preferences": {k: v for k, v in preferences.items() if v},
        "known_about_traveller": memories[:6],
    }
    return [{"role": "system", "content": system}, {"role": "user", "content": json.dumps(context, ensure_ascii=False, default=str)}]


def _int(value: Any) -> Optional[int]:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return int(number) if number == number else None


def _text(value: Any, limit: int) -> str:
    return " ".join(str(value).split())[:limit] if isinstance(value, (str, int, float)) else ""


def _segments_from(data: dict[str, Any], spec: TripSpec) -> list[dict[str, Any]]:
    raw = data.get("segments")
    if not isinstance(raw, list):
        raise OutlineParseError("The outline has no segments.")
    total = spec.num_days
    parsed: list[dict[str, Any]] = []
    for entry in raw[:MAX_SEGMENTS * 2]:
        if not isinstance(entry, dict):
            continue
        start, end = _int(entry.get("start_day")), _int(entry.get("end_day"))
        if start is None or end is None:
            continue
        parsed.append({"start": start, "end": end, "entry": entry})
    if not parsed:
        raise OutlineParseError("The outline has no usable segments.")
    parsed.sort(key=lambda p: (p["start"], p["end"]))
    parsed = parsed[:MAX_SEGMENTS]

    # Repair to a contiguous cover of 1..total: each segment starts where the previous ended; the last one runs to the end.
    segments: list[dict[str, Any]] = []
    cursor = 1
    for index, item in enumerate(parsed):
        if cursor > total:
            break
        end = total if index == len(parsed) - 1 else max(cursor, min(item["end"], total))
        entry = item["entry"]
        highlights = [_text(h, 80) for h in (entry.get("highlights") or []) if _text(h, 80)][:4] if isinstance(entry.get("highlights"), list) else []
        share = entry.get("budget_share")
        share = float(share) if isinstance(share, (int, float)) and share >= 0 else None
        segments.append({
            "start_day": cursor, "end_day": end,
            "area": _text(entry.get("area"), 200) or spec.destination,
            "theme": _text(entry.get("theme"), 160), "highlights": highlights, "_share": share,
        })
        cursor = end + 1
    if cursor <= total:                                   # the model stopped early: extend the last stay
        segments[-1]["end_day"] = total
    _normalize_shares(segments, total)
    return segments


def _normalize_shares(segments: list[dict[str, Any]], total_days: int) -> None:
    given = [s["_share"] for s in segments]
    if all(g is not None for g in given) and sum(given) > 0:
        weights = given
    else:                                                  # missing or invalid: proportional to length of stay
        weights = [(s["end_day"] - s["start_day"] + 1) / total_days for s in segments]
    whole = sum(weights)
    for seg, weight in zip(segments, weights):
        seg["budget_share"] = round(weight / whole, 4)
        seg.pop("_share", None)


def parse_outline(raw: str, spec: TripSpec) -> dict[str, Any]:
    data = extract_json_object(raw)
    return new_outline(spec, segments=_segments_from(data, spec), overview=_text(data.get("overview"), 1200), generated_by="ai")


def new_outline(spec: TripSpec, *, segments: list[dict[str, Any]], overview: str, generated_by: str) -> dict[str, Any]:
    return {
        "version": OUTLINE_VERSION, "total_days": spec.num_days, "start_date": spec.start_date.isoformat(),
        "destination": spec.destination, "overview": overview, "segments": segments,
        "chunks_planned": [], "summaries": {}, "used_titles": [], "generated_by": generated_by,
    }


def fallback_outline(spec: TripSpec) -> dict[str, Any]:
    """No AI needed: one stay in the destination for the whole trip. Used when the outline call fails, so a long trip
    can still be planned (the detailed plans just are not split across areas)."""
    segment = {"start_day": 1, "end_day": spec.num_days, "area": spec.destination, "theme": "", "highlights": [], "budget_share": 1.0}
    return new_outline(spec, segments=[segment], overview="", generated_by="fallback")


def outline_is_current(outline: Optional[dict[str, Any]], spec: TripSpec) -> bool:
    """False when the trip's dates or destination changed after the outline was made (it must then be rebuilt)."""
    return bool(
        isinstance(outline, dict) and outline.get("version") == OUTLINE_VERSION
        and outline.get("total_days") == spec.num_days and outline.get("start_date") == spec.start_date.isoformat()
        and outline.get("destination") == spec.destination and isinstance(outline.get("segments"), list) and outline["segments"]
    )


# --- cache (shared, non-personal) ------------------------------------------------------------------------------------
def outline_cache_key(spec: TripSpec, preferences: dict[str, Any], language: str) -> str:
    """Same destination, length, style and language => same route. Dates, budget amount and traveller identity are left
    out on purpose: the cached value holds only fractions and place names."""
    basis = {
        "d": spec.destination.strip().lower(), "n": spec.num_days, "t": spec.travelers > 1, "lang": language,
        "style": str(preferences.get("travel_style") or "").lower(), "pace": str(preferences.get("pace") or "").lower(),
        "interests": sorted(str(i).lower() for i in (preferences.get("interests") or [])),
    }
    return "outline:v1:" + hashlib.sha256(json.dumps(basis, sort_keys=True).encode()).hexdigest()[:32]


def to_cache_value(outline: dict[str, Any]) -> dict[str, Any]:
    return {"overview": outline.get("overview", ""), "segments": outline.get("segments", [])}


def from_cache_value(cached: Any, spec: TripSpec) -> Optional[dict[str, Any]]:
    try:
        segments = _segments_from({"segments": cached.get("segments")}, spec)
        return new_outline(spec, segments=segments, overview=_text(cached.get("overview"), 1200), generated_by="cache")
    except (OutlineParseError, AttributeError, TypeError):
        return None
