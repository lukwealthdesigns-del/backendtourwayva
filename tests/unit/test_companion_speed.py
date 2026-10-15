"""Companion speed rules: the right-sized model for the job, and a short conversation history."""
from enum import Enum

from app.modules.companion.speed import MAX_HISTORY_CHARS, MAX_OLD_MESSAGE_CHARS, reply_tier, trim_history


class _Intent(str, Enum):
    ITINERARY_EDIT = "ITINERARY_EDIT"
    GENERAL_TRAVEL = "GENERAL_TRAVEL"
    HOTEL_SEARCH = "HOTEL_SEARCH"


def test_only_itinerary_edits_use_the_strong_model():
    assert reply_tier(_Intent.ITINERARY_EDIT) == "default"
    assert reply_tier(_Intent.GENERAL_TRAVEL) == "fast" and reply_tier(_Intent.HOTEL_SEARCH) == "fast"
    assert reply_tier("UNKNOWN") == "fast"


def _msgs(sizes):
    return [{"role": "user" if i % 2 == 0 else "assistant", "content": "x" * n} for i, n in enumerate(sizes)]


def test_short_conversations_pass_through_untouched():
    m = _msgs([20, 30, 25])
    assert trim_history(m) == m
    assert trim_history([]) == []


def test_the_newest_message_is_never_cut_however_long():
    m = _msgs([50, 60, 20000])
    out = trim_history(m)
    assert out[-1]["content"] == "x" * 20000


def test_old_long_answers_are_shortened_and_the_total_stays_in_budget():
    m = _msgs([200, 5000, 200, 5000, 200, 5000, 200, 300])
    out = trim_history(m)
    assert all(len(x["content"]) <= MAX_OLD_MESSAGE_CHARS + 1 for x in out[:-1])
    assert sum(len(x["content"]) for x in out) <= MAX_HISTORY_CHARS + 300
    assert out[-1] == m[-1]                                      # the question being answered, untouched


def test_oldest_messages_are_dropped_first_and_the_last_exchange_is_always_kept():
    m = _msgs([800] * 30)
    out = trim_history(m)
    assert len(out) < len(m) and out[-1] == m[-1] and out[-2] == m[-2]
    assert out == m[-len(out):]                                  # a contiguous recent tail, order preserved
    assert len(trim_history(_msgs([9000, 9000]))) == 2           # even two huge messages keep the last exchange
