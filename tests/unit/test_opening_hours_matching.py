"""Name matching for opening-hours lookups (app/providers/opening_hours/matching.py).

Coordinates alone are not enough in a dense city, so hours are only attached
when the itinerary item's name and the OSM element's name clearly agree. A
missed match costs nothing; a wrong match puts another business's hours on
the plan — so these tests lean on real-world false-positive shapes.
"""
from app.providers.opening_hours.matching import best_match, names_match, normalize_name, significant_tokens


def test_normalize_strips_accents_punctuation_and_case():
    assert normalize_name("Café Zürich!!") == "cafe zurich"
    assert normalize_name("  The   Louvre  ") == "the louvre"


def test_significant_tokens_drops_stop_words_and_short_tokens():
    assert significant_tokens("The Louvre Museum") == {"louvre", "museum"}
    assert significant_tokens("Skip the Line Ticket") == set()   # every word is a stop/weak word or <3 chars


def test_a_ticket_or_tour_suffix_still_matches_the_bare_venue_name():
    assert names_match("Eiffel Tower Summit Access Ticket", "Eiffel Tower")
    assert names_match("Sagrada Família Guided Tour", "Sagrada Familia")
    assert names_match("Louvre Museum Skip-the-Line Entry", "Louvre")


def test_one_shared_generic_word_is_not_enough():
    assert not names_match("Louvre Museum Guided Tour", "Museum Cafe")
    assert not names_match("City Park Walking Tour", "City Hall")


def test_partial_overlap_needs_real_agreement_not_just_any_shared_word():
    assert not names_match("Central Park Bike Tour", "Central Station")
    assert names_match("Notre-Dame Cathedral Tour", "Notre Dame Cathedral")


def test_empty_or_unrelated_names_never_match():
    assert not names_match("", "Eiffel Tower")
    assert not names_match("Eiffel Tower", "")
    assert not names_match("Eiffel Tower", "Colosseum")


def test_best_match_prefers_the_most_specific_agreement():
    elements = [
        {"tags": {"name": "Museum Cafe", "opening_hours": "Mo-Su 08:00-17:00"}},
        {"tags": {"name": "Eiffel Tower", "opening_hours": "Mo-Su 09:30-23:00"}},
        {"tags": {"name": "Eiffel Tower Gift Shop", "opening_hours": "Mo-Su 10:00-19:00"}},
    ]
    match = best_match("Eiffel Tower Summit Access Ticket", elements)
    assert match["tags"]["name"] == "Eiffel Tower"          # 2-word agreement beats the 1-word "Gift Shop" candidate


def test_best_match_ignores_elements_with_no_or_blank_opening_hours():
    elements = [{"tags": {"name": "Eiffel Tower"}}, {"tags": {"name": "Eiffel Tower Annex", "opening_hours": "   "}}]
    assert best_match("Eiffel Tower", elements) is None


def test_best_match_checks_alternate_name_tags():
    elements = [{"tags": {"name:en": "Sagrada Familia", "opening_hours": "Mo-Su 09:00-18:00"}}]
    assert best_match("Sagrada Família Tour", elements) is not None


def test_best_match_returns_none_when_nothing_agrees():
    elements = [{"tags": {"name": "Random Cafe", "opening_hours": "Mo-Su 08:00-17:00"}}]
    assert best_match("Louvre Pyramid", elements) is None


def test_best_match_handles_an_empty_element_list():
    assert best_match("Eiffel Tower", []) is None
