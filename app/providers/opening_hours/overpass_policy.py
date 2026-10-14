"""
Pure policy for being a good citizen of the public Overpass API (stdlib only, unit-tested without the web stack).

The public service is shared and rate limited. Lookups for hotels and attractions in the same neighbourhood used to send one query
each, all at once, and were answered with 429 (slow down) and 504 (busy). The rules here: one query per small map CELL (its answer is
shared by every venue in it), a pause after a refusal, and a slim copy of the answer to cache.
"""
from __future__ import annotations

import email.utils
import time
from typing import Any, Optional

CELL_DECIMALS = 3                      # 0.001 degree is about 110 m: venues this close share one query and one cached answer
CELL_MARGIN_M = 120                    # the query radius grows by this much so a venue anywhere in the cell is still covered
KEEP_TAGS = ("name", "name:en", "official_name", "alt_name", "short_name", "brand", "opening_hours")
MAX_COOLDOWN_S = 300.0


def cell_of(latitude: float, longitude: float) -> tuple[float, float]:
    """The centre of the cell a point falls in (what we actually query)."""
    return round(latitude, CELL_DECIMALS), round(longitude, CELL_DECIMALS)


def cell_cache_key(latitude: float, longitude: float) -> str:
    lat, lon = cell_of(latitude, longitude)
    return f"openinghours:cell:{lat:.{CELL_DECIMALS}f}:{lon:.{CELL_DECIMALS}f}"


def slim_elements(elements: list[Any]) -> list[dict[str, Any]]:
    """Only the tags matching needs, so a cached cell stays small."""
    out = []
    for e in elements:
        if not isinstance(e, dict):
            continue
        tags = e.get("tags") if isinstance(e.get("tags"), dict) else {}
        kept = {k: tags[k] for k in KEEP_TAGS if isinstance(tags.get(k), str) and tags[k].strip()}
        if kept.get("opening_hours"):
            out.append({"tags": kept})
    return out


def retry_after_seconds(value: Optional[str], *, now: Optional[float] = None) -> Optional[float]:
    """Retry-After is either a number of seconds or an HTTP date."""
    if not value:
        return None
    value = value.strip()
    if value.isdigit():
        return float(value)
    try:
        when = email.utils.parsedate_to_datetime(value).timestamp()
    except (TypeError, ValueError):
        return None
    return max(0.0, when - (now if now is not None else time.time()))


def cooldown_seconds(status: Optional[int], retry_after: Optional[str] = None) -> float:
    """How long to leave Overpass alone after a failure. 429: as told (default 60 s). 504/503: it is busy (30 s). Anything else: none."""
    if status == 429:
        told = retry_after_seconds(retry_after)
        return min(told if told is not None else 60.0, MAX_COOLDOWN_S)
    if status in (502, 503, 504):
        return 30.0
    return 0.0
