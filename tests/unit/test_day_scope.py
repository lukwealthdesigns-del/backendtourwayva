"""Single-day regeneration: which days a proposal touches, and the instruction that confines it to one day."""
from types import SimpleNamespace

from app.modules.planning.day_scope import MAX_INSTRUCTION_CHARS, build_instruction, days_touched


def _diff(added=(), removed=(), retimed=(), moved=()):
    return SimpleNamespace(added=list(added), removed=list(removed), retimed=list(retimed), moved=list(moved))


def test_a_change_inside_one_day_touches_only_that_day():
    assert days_touched(_diff(added=[(3, "Museum")], removed=[(3, "Old tour")], retimed=[(3, "Lunch")])) == {3}


def test_a_move_touches_both_days_it_involves():
    assert days_touched(_diff(moved=[("Beach", 2, 4)])) == {2, 4}


def test_an_empty_diff_touches_nothing():
    assert days_touched(_diff()) == set()


def test_a_proposal_that_strays_beyond_the_chosen_day_is_detectable():
    diff = _diff(added=[(3, "Museum")], retimed=[(5, "Dinner")])
    assert days_touched(diff) - {3} == {5}


def test_the_instruction_names_the_day_and_forbids_touching_others():
    text = build_instruction(3, "2026-12-03", None)
    assert "Day 3" in text and "2026-12-03" in text and "every other day exactly as it is" in text


def test_the_users_note_is_included_and_everything_is_capped():
    assert "more relaxed" in build_instruction(2, "d", "  more relaxed  ")
    assert len(build_instruction(2, "d", "x" * 5000)) <= MAX_INSTRUCTION_CHARS
