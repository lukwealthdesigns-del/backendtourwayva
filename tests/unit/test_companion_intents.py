"""Intent routing rules and per-intent policy (Master Prompt §83, §36, §40)."""
import pytest

from app.core.constants import FeatureFlag
from app.modules.companion.intents import (
    INTENT_FEATURE,
    INTENT_TOOLS,
    NEEDS_TRIP_CONTEXT,
    Intent,
    classify_by_rules,
    parse_intent,
)

CASES = [
    # the exact commands from the blueprint (§18)
    ("Make this trip cheaper.", Intent.ITINERARY_EDIT),
    ("Change my hotel.", Intent.ITINERARY_EDIT),
    ("Reduce walking.", Intent.ITINERARY_EDIT),
    ("Add more museums to my trip", Intent.ITINERARY_EDIT),
    ("Remove Day 3.", Intent.ITINERARY_EDIT),
    ("Make the trip more relaxed.", Intent.ITINERARY_EDIT),
    ("Replace this restaurant.", Intent.ITINERARY_EDIT),
    ("Move this activity to tomorrow.", Intent.ITINERARY_EDIT),
    # travel history (§45)
    ("What was my last trip to France?", Intent.TRAVEL_HISTORY),
    ("How much did that trip cost?", Intent.TRAVEL_HISTORY),
    ("Where have I been this year?", Intent.TRAVEL_HISTORY),
    # everything else
    ("Find me a hotel in Lisbon for 3 nights", Intent.HOTEL_SEARCH),
    ("Any flights from Lagos to London next week?", Intent.FLIGHT_SEARCH),
    ("What's the weather like in Paris in October?", Intent.WEATHER_QUERY),
    ("Convert 500 USD to NGN", Intent.CURRENCY_QUERY),
    ("what is the exchange rate for euros", Intent.CURRENCY_QUERY),
    ("Where can I go with a budget of 700000 naira for a week?", Intent.DISCOVERY),
    ("What are the best things to do in Rome?", Intent.ACTIVITY_SEARCH),
    ("What's planned for day 2?", Intent.TRIP_QUERY),
    ("Show me my itinerary", Intent.TRIP_QUERY),
    ("Am I on the premium subscription?", Intent.ACCOUNT_QUERY),
    ("What's my username?", Intent.ACCOUNT_QUERY),
]


@pytest.mark.parametrize("text,expected", CASES)
def test_rules_classify_the_obvious_requests(text, expected):
    assert classify_by_rules(text) == expected


@pytest.mark.parametrize("text", ["hello!", "Tell me about Japanese culture", "is tipping expected abroad?", "", "   "])
def test_ambiguous_messages_fall_through_to_the_model(text):
    assert classify_by_rules(text) is None


def test_specific_rules_beat_generic_ones():
    # mentions both a hotel and the trip, but the user is asking to CHANGE it
    assert classify_by_rules("Change the hotel in my trip") == Intent.ITINERARY_EDIT
    # weather mentions the trip, but is a weather question
    assert classify_by_rules("Will it rain during my trip?") == Intent.WEATHER_QUERY


@pytest.mark.parametrize("raw,expected", [
    ("HOTEL_SEARCH", Intent.HOTEL_SEARCH),
    ("  weather_query\n", Intent.WEATHER_QUERY),
    ('{"intent": "ITINERARY_EDIT"}', Intent.ITINERARY_EDIT),
    ("I think this is TRIP_QUERY.", Intent.TRIP_QUERY),
    ("banana", Intent.UNKNOWN),
    ("", Intent.UNKNOWN),
    (None, Intent.UNKNOWN),
])
def test_parse_intent_is_tolerant_and_never_guesses(raw, expected):
    assert parse_intent(raw) == expected


def test_only_itinerary_edit_may_propose_changes():
    for intent, tools in INTENT_TOOLS.items():
        proposes = [t for t in tools if t.startswith("propose_") or t == "create_trip_version"]
        assert (intent == Intent.ITINERARY_EDIT) == bool(proposes), intent


def test_every_intent_has_a_tool_set_and_no_intent_gets_account_write_tools():
    assert set(INTENT_TOOLS) == set(Intent)
    forbidden = {"delete_account", "change_subscription", "create_admin", "run_sql"}
    assert not any(set(tools) & forbidden for tools in INTENT_TOOLS.values())


def test_feature_gating_map():
    assert INTENT_FEATURE[Intent.HOTEL_SEARCH] == FeatureFlag.HOTELS
    assert INTENT_FEATURE[Intent.FLIGHT_SEARCH] == FeatureFlag.FLIGHTS
    assert INTENT_FEATURE[Intent.ITINERARY_EDIT] == FeatureFlag.PLANNER
    assert INTENT_FEATURE[Intent.DISCOVERY] == FeatureFlag.DISCOVER
    assert Intent.GENERAL_TRAVEL not in INTENT_FEATURE and Intent.CURRENCY_QUERY not in INTENT_FEATURE


def test_trip_context_is_loaded_only_where_it_matters():
    assert Intent.CURRENCY_QUERY not in NEEDS_TRIP_CONTEXT and Intent.ACCOUNT_QUERY not in NEEDS_TRIP_CONTEXT
    assert Intent.ITINERARY_EDIT in NEEDS_TRIP_CONTEXT
