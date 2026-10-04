"""app/utils/opening_hours.py — the OSM `opening_hours` subset parser.

Every assertion here was exercised by direct execution while writing the
module; kept as a permanent regression suite. The single rule that matters
most: unsupported syntax must return None ("unknown"), never a guess.
"""
from datetime import time

from app.utils.opening_hours import parse_opening_hours as P
from app.utils.opening_hours import time_to_minutes


def _describe(raw, weekday):
    hours = P(raw)
    return hours.describe(weekday) if hours else None


def test_24_7_is_open_every_day_all_day():
    hours = P("24/7")
    assert hours.is_open(6, 3 * 60) and hours.is_open(0, 0) and hours.is_open(3, 23 * 60 + 59)


def test_a_simple_weekday_range():
    assert _describe("Mo-Fr 09:00-18:00", 0) == "09:00–18:00"
    assert _describe("Mo-Fr 09:00-18:00", 4) == "09:00–18:00"
    assert _describe("Mo-Fr 09:00-18:00", 5) == "closed"   # a day no rule names is closed


def test_multiple_ranges_in_one_day():
    hours = P("Mo-Fr 09:00-12:00,14:00-18:00")
    assert hours.is_open(1, 10 * 60) and not hours.is_open(1, 13 * 60) and hours.is_open(1, 14 * 60)
    assert not hours.is_open(1, 18 * 60)                    # end is exclusive


def test_an_additive_comma_rule_adds_another_day_without_erasing_the_first():
    hours = P("Mo-Fr 09:00-12:00, Sa 10:00-14:00")
    assert hours.is_open(0, 9 * 60) and hours.is_open(5, 11 * 60) and not hours.is_open(6, 11 * 60)


def test_a_weekday_list():
    assert _describe("Mo,We,Fr 09:00-17:00", 2) == "09:00–17:00"
    assert _describe("Mo,We,Fr 09:00-17:00", 1) == "closed"


def test_a_later_semicolon_rule_overrides_only_the_days_it_names():
    assert _describe("Mo-Su 09:00-17:00; Sa 10:00-12:00", 5) == "10:00–12:00"
    assert _describe("Mo-Su 09:00-17:00; Sa 10:00-12:00", 4) == "09:00–17:00"


def test_off_clears_the_named_days():
    assert _describe("Mo-Su 09:00-17:00; Su off", 6) == "closed"
    assert _describe("Mo-Su 09:00-17:00; Su off", 0) == "09:00–17:00"


def test_public_holiday_selectors_are_recognized_and_ignored_not_rejected():
    assert _describe("Mo-Su 09:00-17:00; PH off", 2) == "09:00–17:00"
    assert _describe("Mo-Fr 09:00-17:00, PH 10:00-12:00", 0) == "09:00–17:00"
    assert P("PH off") is None                              # nothing positive left to check against


def test_wrap_around_day_ranges():
    hours = P("Sa-Mo 10:00-16:00")
    assert [hours.describe(i) for i in range(7)] == [
        "10:00–16:00", "closed", "closed", "closed", "closed", "10:00–16:00", "10:00–16:00",
    ]


def test_ranges_crossing_midnight_spill_into_the_next_day():
    hours = P("Fr,Sa 22:00-02:00")
    assert hours.is_open(4, 23 * 60) and hours.is_open(5, 60) and not hours.is_open(5, 3 * 60) and hours.is_open(6, 60)
    assert hours.closing_minute(4, 23 * 60) == 24 * 60 + 2 * 60
    assert hours.describe(5).startswith("00:00–02:00")


def test_24_00_is_accepted_only_as_an_end_time():
    assert P("Mo-Fr 08:00-24:00").closing_minute(0, 9 * 60) == 24 * 60
    assert P("Mo-Fr 24:00-08:00") is None                   # 24:00 is not a valid START


def test_closing_minute_is_none_when_not_open_at_that_moment():
    hours = P("Mo-Fr 09:00-18:00")
    assert hours.closing_minute(0, 10 * 60) == 18 * 60
    assert hours.closing_minute(0, 8 * 60) is None


def test_time_to_minutes_matches_a_plain_time_object():
    assert time_to_minutes(time(9, 30)) == 570


UNPARSEABLE = [
    None, "", "   ",
    "Jan-Mar Mo-Fr 09:00-17:00",       # months: out of scope
    "sunrise-sunset",                  # solar times: out of scope
    "Mo-Fr 09:00+",                    # open-ended: out of scope
    'Mo-Fr 09:00-17:00 "call ahead"',  # free-text comment
    "by appointment",
    "week 1-10 Mo 09:00-12:00",        # week numbers: out of scope
    "Mo-Fr",                           # a selector with nothing to say
    "Mo-Fr 25:00-26:00",               # not valid clock times
    "Mo 09:00-09:00",                  # zero-length range
    "Mo-Su open",
    "Mo-Fr 09:00-17:00; Easter off",   # named-holiday selector beyond PH/SH
    "2026 Mo 09:00-12:00",             # a bare year
    "Mo-Su off",                       # nothing positive
    "Mo-Fr 09:00-18:00 nonsense",
    "Mon-Fri 09:00-18:00",             # not OSM's two-letter day codes
]


def test_unsupported_or_malformed_syntax_is_always_none_never_a_guess():
    for raw in UNPARSEABLE:
        assert P(raw) is None, raw


def test_a_fully_off_schedule_after_parsing_the_rules_is_none():
    assert P("Mo-Su off") is None
