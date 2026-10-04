"""Parser and evaluator for a well-defined SUBSET of the OpenStreetMap
`opening_hours` syntax (https://wiki.openstreetmap.org/wiki/Key:opening_hours).

Pure and dependency-free so every rule is unit-testable. The contract that
matters is the one in `parse_opening_hours`: it returns a result ONLY when it
fully understood the string, and None otherwise. "I could not interpret this"
must never turn into "this place is closed" (Blueprint §108 / §26: no
deterministic claims on low-confidence data), so anything outside the supported
subset — months, week numbers, sunrise/sunset, "+" open-ended times, quoted
comments, "by appointment" and similar — yields None rather than a guess.

Supported
  * `24/7`
  * weekday selectors: `Mo`, `Mo-Fr`, `Mo,We,Fr`, wrap-around `Sa-Mo`
  * one or more time ranges per rule: `09:00-12:00,14:00-18:00`
  * ranges crossing midnight: `22:00-02:00` (the spill counts for the NEXT day)
  * `24:00` as an end time
  * `off` / `closed` (clears the days it names)
  * `;`-separated rules, where a later rule OVERRIDES the days it names
  * `,`-separated additional rules inside one `;` rule (`Mo-Fr 09:00-12:00, Sa 10:00-14:00`)
  * public/school-holiday selectors (`PH`, `SH`) are recognised and IGNORED: we
    cannot know holidays, so a holiday-only rule contributes nothing and the
    weekday rules stand.

A weekday that no rule mentions is closed, which is what the tag means.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import time
from typing import Optional

WEEKDAY_CODES = ("Mo", "Tu", "We", "Th", "Fr", "Sa", "Su")
WEEKDAY_NAMES = ("Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday")
_DAY_INDEX = {code: i for i, code in enumerate(WEEKDAY_CODES)}
_MINUTES_PER_DAY = 24 * 60

# Anything that changes meaning in a way this subset does not model.
_UNSUPPORTED = re.compile(
    r"sunrise|sunset|dawn|dusk|week|easter|open|appointment|\bby\b|\bon\b|"
    r"\b(?:jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*\b|\d{4}\b(?!:)|[\"+\[\]<>=*]",
    re.IGNORECASE,
)

_TOKEN = re.compile(
    r"(?P<time>\d{1,2}:\d{2}\s*-\s*\d{1,2}:\d{2})"
    r"|(?P<day>(?:Mo|Tu|We|Th|Fr|Sa|Su)(?:\s*-\s*(?:Mo|Tu|We|Th|Fr|Sa|Su))?)"
    r"|(?P<hol>PH|SH)"
    r"|(?P<off>off|closed)"
    r"|(?P<comma>,)"
    r"|(?P<space>\s+)",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class OpeningHours:
    """`ranges[d]` are (start, end) minutes for OPENING on weekday d (0 = Monday),
    end capped at 1440. `spill[d]` are (0, end) minutes that late-night ranges
    from weekday d run into the FOLLOWING day."""

    ranges: tuple[tuple[tuple[int, int], ...], ...]
    spill: tuple[tuple[tuple[int, int], ...], ...]
    raw: str

    def _open_ranges(self, weekday: int) -> list[tuple[int, int]]:
        return sorted(self.ranges[weekday] + self.spill[(weekday - 1) % 7])

    def is_open(self, weekday: int, minute: int) -> bool:
        return any(start <= minute < end for start, end in self._open_ranges(weekday))

    def closing_minute(self, weekday: int, minute: int) -> Optional[int]:
        """When the opening interval containing `minute` ends, counting a
        continuation past midnight; None if it is not open at `minute`."""
        for start, end in self._open_ranges(weekday):
            if start <= minute < end:
                if end == _MINUTES_PER_DAY:
                    end += max((e for _, e in self.spill[weekday]), default=0)
                return end
        return None

    def describe(self, weekday: int) -> str:
        ranges = self._open_ranges(weekday)
        if not ranges:
            return "closed"
        return ", ".join(f"{_fmt(s)}–{_fmt(e)}" for s, e in ranges)


def _fmt(minutes: int) -> str:
    return f"{minutes // 60:02d}:{minutes % 60:02d}"


def _to_minutes(value: str, *, allow_24: bool) -> Optional[int]:
    hours, minutes = value.split(":")
    h, m = int(hours), int(minutes)
    if m > 59 or h > 24 or (h == 24 and (m != 0 or not allow_24)):
        return None
    return h * 60 + m


def _expand_days(token: str) -> Optional[list[int]]:
    parts = [p.strip().title() for p in re.split(r"\s*-\s*", token.strip())]
    if any(p not in _DAY_INDEX for p in parts):
        return None
    if len(parts) == 1:
        return [_DAY_INDEX[parts[0]]]
    start, end = _DAY_INDEX[parts[0]], _DAY_INDEX[parts[1]]
    length = (end - start) % 7 + 1          # Sa-Mo wraps: Sa, Su, Mo
    return [(start + i) % 7 for i in range(length)]


@dataclass
class _SubRule:
    days: list[int]
    holiday_only: bool
    off: bool
    times: list[tuple[int, int]]


def _parse_sub_rules(rule: str) -> Optional[list[_SubRule]]:
    """Splits one `;`-separated rule into its comma-additive sub-rules."""
    if rule.strip() == "24/7":
        return [_SubRule(days=list(range(7)), holiday_only=False, off=False, times=[(0, _MINUTES_PER_DAY)])]

    subs: list[_SubRule] = []
    days: list[int] = []
    has_holiday = False
    off = False
    times: list[tuple[int, int]] = []
    in_times = False
    position = 0

    def finish() -> bool:
        nonlocal days, has_holiday, off, times, in_times
        if not (times or off):
            return False                       # a selector with nothing to say (e.g. "Mo-Fr")
        subs.append(_SubRule(days=days, holiday_only=has_holiday and not days, off=off, times=times))
        days, has_holiday, off, times, in_times = [], False, False, [], False
        return True

    while position < len(rule):
        match = _TOKEN.match(rule, position)
        if match is None:
            return None                         # unknown syntax: refuse to guess
        position = match.end()
        kind = match.lastgroup
        if kind == "space":
            continue
        if kind == "comma":
            continue
        if kind in ("day", "hol"):
            if in_times and not finish():       # a selector after times begins a NEW additive rule
                return None
            if kind == "hol":
                has_holiday = True
            else:
                expanded = _expand_days(match.group("day"))
                if expanded is None:
                    return None
                days.extend(expanded)
        elif kind == "off":
            off, in_times = True, True
        elif kind == "time":
            start_text, end_text = re.split(r"\s*-\s*", match.group("time"))
            start, end = _to_minutes(start_text, allow_24=False), _to_minutes(end_text, allow_24=True)
            if start is None or end is None or start == end:
                return None
            times.append((start, end))
            in_times = True
    if not finish():
        return None
    return subs


def parse_opening_hours(raw: Optional[str]) -> Optional[OpeningHours]:
    """Returns the parsed schedule, or None when the string is empty, uses syntax
    outside the supported subset, or would not tell us a single open interval.
    None always means "unknown", never "closed"."""
    if not raw or not raw.strip() or _UNSUPPORTED.search(raw.replace("24/7", "")):
        return None

    ranges: list[list[tuple[int, int]]] = [[] for _ in range(7)]
    spill: list[list[tuple[int, int]]] = [[] for _ in range(7)]

    def clear(day: int) -> None:
        ranges[day], spill[day] = [], []

    def add(day: int, start: int, end: int) -> None:
        if end > start:
            ranges[day].append((start, end))
        else:                                   # crosses midnight: tail belongs to the next day
            ranges[day].append((start, _MINUTES_PER_DAY))
            if end > 0:
                spill[day].append((0, end))

    for rule in (r.strip() for r in raw.split(";")):
        if not rule:
            continue
        subs = _parse_sub_rules(rule)
        if subs is None:
            return None
        for index, sub in enumerate(subs):
            if sub.holiday_only:
                continue                        # we cannot know holidays; weekday rules stand
            target_days = sub.days or list(range(7))
            if index == 0:                      # a `;` rule overrides the days it names
                for day in target_days:
                    clear(day)
            if sub.off:
                if index != 0:
                    for day in target_days:
                        clear(day)
                continue
            for day in target_days:
                for start, end in sub.times:
                    add(day, start, end)

    if not any(ranges):
        return None                             # nothing positive to check against

    return OpeningHours(
        ranges=tuple(tuple(sorted(r)) for r in ranges),
        spill=tuple(tuple(sorted(s)) for s in spill),
        raw=raw.strip(),
    )


def time_to_minutes(value: time) -> int:
    return value.hour * 60 + value.minute
