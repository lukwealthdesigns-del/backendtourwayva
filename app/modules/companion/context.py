"""
Companion prompt/context assembly (Master Prompt §36).

Context hierarchy: current conversation → current trip → trip history →
preferences → memory → shared RAG → live provider data. Only what the
detected intent can use is loaded ("do not retrieve everything for every
request"); everything is formatted here as compact text. Pure functions,
duck-typed on the ORM objects, so they are unit-tested without a database.
"""
from __future__ import annotations

from typing import Any, Iterable, Optional

from app.modules.companion.intents import INTENT_GUIDANCE, Intent

BASE_SYSTEM_PROMPT = (
    "You are the Tour-Wayva Companion, a helpful and concise AI travel assistant. "
    "You help with general travel questions, destination advice, and questions about the user's trips. "
    "Use your tools whenever an answer depends on real, current, personal or specific data instead of guessing. "
    "You can PROPOSE changes to a trip's itinerary, but proposals never take effect immediately: they create a "
    "suggestion the user must explicitly confirm. Always say plainly that you have proposed a change and that it "
    "needs their confirmation — never imply the trip has already been changed. "
    "Never state prices, availability, flight details, weather or exchange rates that did not come from a tool. "
    "Use the personal facts below only when relevant to what the user asks; do not force them in. "
    "If a tool returns an error or you do not know something factual, say so plainly rather than guessing."
)

MAX_TRIP_CONTEXT_CHARS = 3500


def _fmt_time(value: Any) -> str:
    return value.strftime("%H:%M") if value else ""


def format_trip_context(trip: Any, days_with_items: Iterable[tuple[Any, list[Any]]], *, max_chars: int = MAX_TRIP_CONTEXT_CHARS) -> str:
    budget = f" · budget {trip.budget_amount:g} {trip.budget_currency}" if getattr(trip, "budget_amount", None) else ""
    header = (
        f"Current trip (id {trip.id}): {trip.destination} · {trip.start_date.isoformat()} to "
        f"{trip.end_date.isoformat()} · {trip.travelers} traveller(s){budget}"
    )
    lines = [header]
    days = list(days_with_items)
    shown = 0
    for day, items in days:
        weather = f" [{day.weather_summary}]" if getattr(day, "weather_summary", None) else ""
        block = [f"Day {day.day_number} ({day.date.isoformat()}){weather}:"]
        if not items:
            block.append("  (nothing planned)")
        for item in items:
            when = f"{_fmt_time(item.start_time)}-{_fmt_time(item.end_time)} " if item.start_time else ""
            kind = item.item_type.value if hasattr(item.item_type, "value") else str(item.item_type)
            cost = f", {item.estimated_cost:g} {item.currency}" if item.estimated_cost else ""
            block.append(f"  - {when}{item.title} ({kind}{cost}) [item {item.id}]")
        candidate = "\n".join(lines + block)
        if len(candidate) > max_chars and shown:
            break
        lines.extend(block)
        shown += 1
    if shown < len(days):
        lines.append(f"... {len(days) - shown} more day(s) not shown; call get_trip_itinerary for the full plan.")
    return "\n".join(lines)


def format_preferences(prefs: Optional[Any]) -> str:
    """Only what the user VOLUNTARILY provided; nothing is inferred."""
    if prefs is None:
        return ""
    parts = []
    for label, value in (
        ("travel styles", ", ".join(prefs.travel_styles or [])),
        ("interests", ", ".join(prefs.interests or [])),
        ("budget level", prefs.budget_preference),
        ("accommodation", prefs.accommodation_preference),
        ("transport", prefs.transportation_preference),
        ("walking", prefs.walking_preference),
        ("dietary needs", ", ".join(prefs.dietary_preferences or [])),
        ("accessibility needs", ", ".join(prefs.accessibility_preferences or [])),
    ):
        if value:
            parts.append(f"{label}: {value}")
    return "; ".join(parts)


def format_memories(facts: list[str], *, limit: int = 10) -> str:
    return "\n".join(f"- {fact}" for fact in facts[:limit])


def assemble_system_prompt(
    intent: Intent,
    *,
    summary: Optional[str] = None,
    trip_context: str = "",
    preferences: str = "",
    memories: str = "",
    language: Optional[str] = None,
) -> str:
    sections = [BASE_SYSTEM_PROMPT]
    guidance = INTENT_GUIDANCE.get(intent)
    if guidance:
        sections.append(guidance)
    if language and language.lower() not in ("en", "en-us", "en-gb"):
        sections.append(f"Reply in the user's language (code: {language}) unless they write in another one.")
    if summary:
        sections.append(f"Summary of earlier conversation:\n{summary}")
    if trip_context:
        sections.append(trip_context)
    if preferences:
        sections.append(f"Stated preferences of this user: {preferences}")
    if memories:
        sections.append(f"Known about this user:\n{memories}")
    return "\n\n".join(sections)
