"""Pure helpers for single-day regeneration (stdlib only, unit-tested without the web stack)."""
from __future__ import annotations

from typing import Any, Optional

MAX_NOTE_CHARS = 240
MAX_INSTRUCTION_CHARS = 600


def days_touched(diff: Any) -> set[int]:
    """Every day number a plan diff changes in any way: an item added to it, removed from it, retimed in it, or moved from/to it.
    `diff` is a PlanDiff (added/removed/retimed are (day, title) pairs, moved is (title, from_day, to_day))."""
    touched = {d for d, _ in diff.added} | {d for d, _ in diff.removed} | {d for d, _ in diff.retimed}
    for _title, from_day, to_day in diff.moved:
        touched.update((from_day, to_day))
    return touched


def build_instruction(day_number: int, day_date: Any, note: Optional[str]) -> str:
    """The instruction for the revision workflow: replace exactly one day, leave every other day as it is."""
    base = (
        f"Replace Day {day_number} ({day_date}) with a completely fresh plan of different suggestions for that day. "
        f"Keep every other day exactly as it is: do not add, remove, move or retime anything on any other day."
    )
    extra = (note or "").strip()[:MAX_NOTE_CHARS]
    text = f"{base} Preference for the new day: {extra}" if extra else base
    return text[:MAX_INSTRUCTION_CHARS]
