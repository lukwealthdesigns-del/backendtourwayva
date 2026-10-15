"""
Pure helpers that keep the Companion quick (stdlib only, unit-tested without the web stack).

Where the time went: every reply used the biggest model, every message re-sent up to twelve full-length earlier messages, and the
same entitlement lookup ran once per tool. Quick chat needs the right-sized model for the job, a short prompt, and one lookup.
"""
from __future__ import annotations

from typing import Any

# Only changing the itinerary needs the strongest model. Lookups, questions and chat are answered well (and much faster) by the fast one.
STRONG_REPLY_INTENTS = frozenset({"ITINERARY_EDIT"})

MAX_HISTORY_CHARS = 6000          # about 1,500 tokens of earlier conversation
MAX_OLD_MESSAGE_CHARS = 900       # an old, long answer is cut: its gist is enough context for the next question
MIN_KEEP = 2                      # always keep at least the last exchange, however long


def reply_tier(intent: Any) -> str:
    """"default" (the strong model) only for itinerary edits, otherwise "fast"."""
    return "default" if getattr(intent, "value", str(intent)) in STRONG_REPLY_INTENTS else "fast"


def trim_history(messages: list[dict[str, Any]], *, max_chars: int = MAX_HISTORY_CHARS, per_old_message: int = MAX_OLD_MESSAGE_CHARS) -> list[dict[str, Any]]:
    """The conversation the model needs, no more. The newest message (the question being answered) is NEVER cut. Older messages are
    shortened when long, then dropped oldest-first once the total passes `max_chars`; the last exchange is always kept."""
    if not messages:
        return []
    out: list[dict[str, Any]] = []
    budget = max_chars
    last_index = len(messages) - 1
    for i in range(last_index, -1, -1):
        m = messages[i]
        content = m.get("content") or ""
        if i != last_index and len(content) > per_old_message:
            content = content[:per_old_message].rstrip() + "…"
        keep_anyway = len(out) < MIN_KEEP
        if not keep_anyway and budget - len(content) < 0:
            break
        budget -= len(content)
        out.append({**m, "content": content} if content != m.get("content") else m)
    out.reverse()
    return out
