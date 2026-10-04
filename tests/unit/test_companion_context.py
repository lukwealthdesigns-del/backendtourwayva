from datetime import date, time
from types import SimpleNamespace as NS

from app.core.constants import TripItemType
from app.modules.companion.context import assemble_system_prompt, format_memories, format_preferences, format_trip_context
from app.modules.companion.intents import Intent

TRIP = NS(id="t-1", destination="Paris", start_date=date(2026, 10, 1), end_date=date(2026, 10, 3), travelers=2,
          budget_amount=1000.0, budget_currency="EUR")


def _day(n, weather=None):
    return NS(day_number=n, date=date(2026, 10, n), weather_summary=weather)


def _item(title, i="i-1", start=time(9, 30), end=time(12), cost=40.0):
    return NS(id=i, title=title, item_type=TripItemType.ACTIVITY, start_time=start, end_time=end,
              estimated_cost=cost, currency="EUR")


def test_trip_context_lists_days_items_and_ids_the_model_needs():
    text = format_trip_context(TRIP, [(_day(1, "Sunny, 18-22°C"), [_item("Louvre")]), (_day(2), [])])
    assert "Paris" in text and "2 traveller(s)" in text and "budget 1000 EUR" in text
    assert "Day 1 (2026-10-01) [Sunny, 18-22°C]:" in text
    assert "09:30-12:00 Louvre (activity, 40 EUR) [item i-1]" in text
    assert "(nothing planned)" in text


def test_long_trips_are_truncated_with_a_pointer_to_the_tool():
    days = [(_day(1 + n % 3), [_item(f"Item {n}-{k}", f"i-{n}-{k}") for k in range(6)]) for n in range(12)]
    text = format_trip_context(TRIP, days, max_chars=900)
    assert len(text) < 1200 and "more day(s) not shown" in text and "get_trip_itinerary" in text


def test_preferences_only_include_what_the_user_provided():
    prefs = NS(travel_styles=["adventure"], interests=[], budget_preference=None, accommodation_preference="hostel",
               transportation_preference=None, walking_preference="low", dietary_preferences=[],
               accessibility_preferences=[])
    assert format_preferences(prefs) == "travel styles: adventure; accommodation: hostel; walking: low"
    assert format_preferences(None) == ""


def test_system_prompt_includes_only_the_sections_it_is_given():
    bare = assemble_system_prompt(Intent.CURRENCY_QUERY)
    assert "Known about this user" not in bare and "Current trip" not in bare and "convert_currency" in bare
    full = assemble_system_prompt(Intent.ITINERARY_EDIT, summary="talked about Paris", trip_context="Current trip (id t)",
                                  preferences="walking: low", memories=format_memories(["likes museums"]), language="fr")
    assert "propose_itinerary_revision" in full and "talked about Paris" in full and "likes museums" in full
    assert "language (code: fr)" in full and "walking: low" in full
    assert "language (code" not in assemble_system_prompt(Intent.GENERAL_TRAVEL, language="en")
