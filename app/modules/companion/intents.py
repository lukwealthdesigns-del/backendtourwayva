"""
Companion intent routing (Master Prompt §83).

  1. cheap deterministic rules classify the obvious requests ("remove day 3",
     "what's the weather in Lisbon") with no model call;
  2. anything ambiguous goes to a one-word model classification (fast tier);
  3. the resulting intent decides which TOOLS the agent may use, which feature
     flag the request needs, and which context is worth loading (§36:
     "do not retrieve everything for every request").

Pure stdlib + the FeatureFlag enum — unit-testable without a database.
"""
from __future__ import annotations

import re
from enum import Enum
from typing import Optional

from app.core.constants import FeatureFlag


class Intent(str, Enum):
    GENERAL_TRAVEL = "GENERAL_TRAVEL"
    TRIP_QUERY = "TRIP_QUERY"
    ITINERARY_EDIT = "ITINERARY_EDIT"
    HOTEL_SEARCH = "HOTEL_SEARCH"
    FLIGHT_SEARCH = "FLIGHT_SEARCH"
    ACTIVITY_SEARCH = "ACTIVITY_SEARCH"
    WEATHER_QUERY = "WEATHER_QUERY"
    CURRENCY_QUERY = "CURRENCY_QUERY"
    TRAVEL_HISTORY = "TRAVEL_HISTORY"
    DISCOVERY = "DISCOVERY"
    ACCOUNT_QUERY = "ACCOUNT_QUERY"
    UNKNOWN = "UNKNOWN"


# Features a request needs IN ADDITION to COMPANION (which gates the endpoint).
INTENT_FEATURE: dict[Intent, FeatureFlag] = {
    Intent.ITINERARY_EDIT: FeatureFlag.PLANNER,
    Intent.HOTEL_SEARCH: FeatureFlag.HOTELS,
    Intent.FLIGHT_SEARCH: FeatureFlag.FLIGHTS,
    Intent.ACTIVITY_SEARCH: FeatureFlag.ACTIVITIES,
    Intent.WEATHER_QUERY: FeatureFlag.WEATHER,
    Intent.DISCOVERY: FeatureFlag.DISCOVER,
}

_READ_ONLY_GENERAL = (
    "search_travel_knowledge", "search_places", "geocode", "get_weather", "convert_currency",
    "get_my_memories", "calculate_route", "get_saved_places",
)

# Tools each intent may use. A tool the model asks for that is not listed is
# refused by the agent node even if it exists elsewhere (defence in depth: the
# tool itself ALSO re-authorizes — §40).
INTENT_TOOLS: dict[Intent, tuple[str, ...]] = {
    Intent.GENERAL_TRAVEL: _READ_ONLY_GENERAL,
    Intent.TRIP_QUERY: ("get_trip", "get_trip_itinerary", "get_weather", "convert_currency",
                        "search_travel_knowledge", "get_my_memories", "calculate_route"),
    Intent.ITINERARY_EDIT: ("get_trip", "get_trip_itinerary", "propose_add_trip_item", "propose_update_trip_item",
                            "propose_delete_trip_item", "propose_itinerary_revision", "create_trip_version",
                            "search_activities", "search_places", "search_hotels", "geocode",
                            "get_saved_places", "save_place", "replace_hotel", "add_flight_to_trip"),
    Intent.HOTEL_SEARCH: ("search_hotels", "get_trip", "geocode", "convert_currency", "get_my_memories",
                          "calculate_route", "save_place"),
    Intent.FLIGHT_SEARCH: ("search_flights", "get_trip", "convert_currency", "get_my_memories", "add_flight_to_trip"),
    Intent.ACTIVITY_SEARCH: ("search_activities", "search_places", "get_trip", "geocode", "get_weather",
                             "get_my_memories", "search_travel_knowledge", "calculate_route", "save_place"),
    Intent.WEATHER_QUERY: ("get_weather", "geocode", "get_trip"),
    Intent.CURRENCY_QUERY: ("convert_currency",),
    Intent.TRAVEL_HISTORY: ("get_trip_history", "get_my_memories", "get_saved_places"),
    Intent.DISCOVERY: ("search_destinations", "search_travel_knowledge", "convert_currency", "get_weather",
                       "get_my_memories", "get_my_profile", "save_place"),
    Intent.ACCOUNT_QUERY: ("get_my_profile", "get_my_memories"),
    Intent.UNKNOWN: _READ_ONLY_GENERAL + ("get_trip", "get_trip_itinerary", "get_my_profile"),  # save_place excluded when unsure
}

# The feature each tool needs. The intent's own feature (above) is not enough on its own:
# UNKNOWN / GENERAL_TRAVEL / TRIP_QUERY offer read tools like get_weather or
# search_activities, and those must respect the user's plan just as the REST endpoints do.
# Enforced twice: the agent only OFFERS entitled tools, and execute_tool re-checks (§40).
TOOL_FEATURES: dict[str, FeatureFlag] = {
    "search_hotels": FeatureFlag.HOTELS,
    "search_flights": FeatureFlag.FLIGHTS,
    "search_activities": FeatureFlag.ACTIVITIES,
    "get_weather": FeatureFlag.WEATHER,
    "search_destinations": FeatureFlag.DISCOVER,
    "get_my_memories": FeatureFlag.MEMORY,
    "propose_add_trip_item": FeatureFlag.PLANNER,
    "propose_update_trip_item": FeatureFlag.PLANNER,
    "propose_delete_trip_item": FeatureFlag.PLANNER,
    "propose_itinerary_revision": FeatureFlag.PLANNER,
    "create_trip_version": FeatureFlag.PLANNER,
    "replace_hotel": FeatureFlag.PLANNER,
    "add_flight_to_trip": FeatureFlag.PLANNER,
}

# Which intents benefit from loading the conversation's trip / the user's stored
# preferences and memories (everything else answers without them).
NEEDS_TRIP_CONTEXT = frozenset({
    Intent.TRIP_QUERY, Intent.ITINERARY_EDIT, Intent.HOTEL_SEARCH, Intent.FLIGHT_SEARCH,
    Intent.ACTIVITY_SEARCH, Intent.WEATHER_QUERY, Intent.UNKNOWN,
})
USES_PERSONALIZATION = frozenset({
    Intent.GENERAL_TRAVEL, Intent.TRIP_QUERY, Intent.ITINERARY_EDIT, Intent.HOTEL_SEARCH, Intent.FLIGHT_SEARCH,
    Intent.ACTIVITY_SEARCH, Intent.DISCOVERY, Intent.UNKNOWN,
})

INTENT_GUIDANCE: dict[Intent, str] = {
    Intent.ITINERARY_EDIT: (
        "The user wants to CHANGE a trip. Look at the itinerary first if you need item ids. For one specific item use "
        "propose_add_trip_item / propose_update_trip_item / propose_delete_trip_item. For broader requests (make it "
        "cheaper, remove a day, more relaxed, less walking, replace a hotel) call propose_itinerary_revision with the "
        "user's request in plain words. Nothing changes until the user confirms — say so clearly."
    ),
    Intent.HOTEL_SEARCH: "The user is looking for accommodation. Use search_hotels; never state a price or availability you did not get from it.",
    Intent.FLIGHT_SEARCH: "The user is looking for flights. Use search_flights; never invent flight numbers, times or prices.",
    Intent.ACTIVITY_SEARCH: "The user wants things to do. Prefer search_activities / search_places over guessing.",
    Intent.WEATHER_QUERY: "Use get_weather; if the date is beyond the forecast range, say the forecast is not available yet.",
    Intent.CURRENCY_QUERY: "Use convert_currency and state the rate's source is live.",
    Intent.TRAVEL_HISTORY: "Answer from get_trip_history only.",
    Intent.DISCOVERY: "The user wants destination ideas within constraints. Use search_destinations with their budget and dates.",
    Intent.ACCOUNT_QUERY: "Only discuss the user's own profile. You cannot change account or billing settings.",
}

# --- Rule-based classification -------------------------------------------------
_R = re.IGNORECASE
_RULES: list[tuple[Intent, re.Pattern[str]]] = [
    (Intent.ITINERARY_EDIT, re.compile(
        r"\b(remove|delete|drop|skip|cancel)\b.{0,40}\b(day\s*\d+|activity|museum|restaurant|hotel|item|stop)\b"
        r"|\b(make|keep|turn)\b.{0,40}\b(trip|plan|itinerary|holiday|vacation)\b.{0,40}"
        r"\b(cheaper|budget|relaxed|relaxing|packed|shorter|longer|kid|family|slower)\b"
        r"|\b(reduce|less|cut down|minimi[sz]e|fewer)\b.{0,20}\bwalking\b"
        r"|\b(move|reschedule|shift|swap|postpone)\b.{0,50}\b(tomorrow|day\s*\d+|morning|afternoon|evening|later|earlier)\b"
        r"|\b(change|replace|swap|switch)\b.{0,25}\b(my |the |this )?(hotel|restaurant|activity|museum)\b"
        r"|\badd (more )?[\w' -]{0,30}\b(to|into|in)\b.{0,15}\b(my |the |this )?(trip|itinerary|plan|day\s*\d+)\b", _R)),
    (Intent.TRAVEL_HISTORY, re.compile(
        r"\b(my|our) (last|previous|past|earlier) (trip|holiday|vacation)\b|\bwhere have i (been|travel(l)?ed)\b"
        r"|\bdid i (ever )?(go|visit|travel)\b|\bhow much did (my|that|the) .{0,20}(trip|holiday)\b", _R)),
    (Intent.CURRENCY_QUERY, re.compile(
        r"\b(exchange rate|convert|currency)\b|\b\d[\d,\.]*\s*[a-z]{3}\s+(to|in|into)\s+[a-z]{3}\b"
        r"|\bhow much is\b.{0,20}\b(in|into)\b\s+[a-z]{3}\b", _R)),
    (Intent.ACCOUNT_QUERY, re.compile(
        r"\bmy (account|profile|username|email|subscription|trial|password)\b|\b(am i|are we) (on|subscribed)\b", _R)),
    (Intent.FLIGHT_SEARCH, re.compile(r"\b(flights?|airfare|airlines?|fly (to|from))\b", _R)),
    (Intent.HOTEL_SEARCH, re.compile(r"\b(hotels?|hostels?|accommodation|where to stay|place to stay|airbnb)\b", _R)),
    (Intent.WEATHER_QUERY, re.compile(r"\b(weather|forecast|temperature|rain(y|ing)?|snow(ing)?|umbrella|sunny)\b", _R)),
    (Intent.DISCOVERY, re.compile(
        r"\bwhere (can|should|could|do) (i|we) (go|travel|holiday|vacation)\b|\b(suggest|recommend)\b.{0,20}\b(destination|place to (go|travel))\b"
        r"|\bi have (a )?(budget of )?[\d,\.]+\b.{0,40}\b(where|travel|trip)\b", _R)),
    (Intent.ACTIVITY_SEARCH, re.compile(
        r"\b(things to do|what to (do|see)|activities|attractions|sightseeing|tours?|restaurants?|where to eat)\b", _R)),
    (Intent.TRIP_QUERY, re.compile(
        r"\b(my|the) (trip|itinerary|plan|schedule)\b|\bday\s*\d+\b|\bwhat('s| is) (planned|on)\b", _R)),
]


def classify_by_rules(text: str) -> Optional[Intent]:
    """First matching rule wins (ordered most specific first); None when the
    message is not clearly any single intent."""
    if not text or not text.strip():
        return None
    for intent, pattern in _RULES:
        if pattern.search(text):
            return intent
    return None


_INTENT_NAMES = {i.value: i for i in Intent}


def parse_intent(raw: str) -> Intent:
    """Read the model's classification reply. Accepts a bare label, a JSON
    object like {"intent": "HOTEL_SEARCH"}, or text containing the label;
    anything else is UNKNOWN (never an exception, never a guess)."""
    if not isinstance(raw, str):
        return Intent.UNKNOWN
    upper = raw.upper()
    hits = [(upper.find(name), intent) for name, intent in _INTENT_NAMES.items() if name in upper]
    # UNKNOWN is a substring of nothing else, but GENERAL_TRAVEL etc. could co-occur; earliest wins.
    return min(hits, key=lambda pair: pair[0])[1] if hits else Intent.UNKNOWN


CLASSIFIER_PROMPT = (
    "Classify the user's message into exactly ONE of these intents and reply with the label only:\n"
    + "\n".join(f"- {i.value}" for i in Intent if i is not Intent.UNKNOWN)
    + "\n- UNKNOWN (only if none fit)\n"
    "ITINERARY_EDIT means changing an existing trip. TRIP_QUERY means asking about an existing trip. "
    "DISCOVERY means asking where to travel."
)
