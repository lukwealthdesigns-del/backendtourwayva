"""Decides whether an OpenStreetMap element is the venue an itinerary item names.

Coordinates alone are not enough in a dense city — the nearest tagged shop is
often not the attraction — so hours are only attached when the NAMES agree.
Deliberately conservative: a missed match costs nothing (no warning), a wrong
match would put another business's hours on the user's plan.
"""
from __future__ import annotations

import re
import unicodedata
from typing import Any, Iterable, Optional

_NAME_TAGS = ("name", "name:en", "alt_name", "official_name", "short_name")
_STOP_WORDS = frozenset({
    "the", "and", "of", "de", "la", "le", "les", "el", "los", "las", "di", "da", "du", "des", "der", "die", "das",
    "a", "an", "in", "at", "to", "for", "on", "with",
    # words that describe how an activity is SOLD, not what the venue is called
    "tour", "tours", "ticket", "tickets", "admission", "entry", "entrance", "guided", "visit", "skip", "line", "access",
    "experience", "combo", "pass", "private", "group", "small", "full", "half", "day",
})


def normalize_name(value: str) -> str:
    ascii_text = unicodedata.normalize("NFKD", value).encode("ascii", "ignore").decode("ascii").lower()
    return re.sub(r"\s+", " ", re.sub(r"[^a-z0-9]+", " ", ascii_text)).strip()


def significant_tokens(value: str) -> set[str]:
    return {t for t in normalize_name(value).split() if len(t) >= 3 and t not in _STOP_WORDS}


# Words that describe WHAT KIND of place something is. They may appear in a
# match, but never carry it on their own ("Museum" alone must not match every museum).
_WEAK_WORDS = frozenset({
    "museum", "gallery", "park", "garden", "gardens", "church", "cathedral", "basilica", "restaurant", "cafe",
    "bar", "pub", "hotel", "tower", "bridge", "palace", "castle", "market", "temple", "mosque", "square", "beach",
    "station", "center", "centre", "national", "city", "old", "town", "street", "road", "bay", "lake", "river",
    "mount", "mountain", "house", "hall", "theatre", "theater", "zoo", "aquarium", "stadium", "shop", "store",
})


def names_match(item_name: str, osm_name: str) -> bool:
    """True when the two names clearly refer to the same venue: they share at
    least one distinctive (non-generic) word, and either one side's words are
    all contained in the other's, or at least two words and half of the smaller
    side overlap. So "Eiffel Tower Summit Ticket" matches OSM's "Eiffel Tower",
    while "Louvre Museum Guided Tour" does not match "Museum Cafe" and "Central
    Park Bike Tour" does not match "Central Station"."""
    a, b = significant_tokens(item_name), significant_tokens(osm_name)
    common = a & b
    if not common or not (common - _WEAK_WORDS):
        return False
    if common == a or common == b:
        return True
    return len(common) >= 2 and len(common) / min(len(a), len(b)) >= 0.5


def best_match(item_name: str, elements: Iterable[dict[str, Any]]) -> Optional[dict[str, Any]]:
    """The element with recorded opening hours whose name agrees with `item_name`.
    Prefers the most specific agreement (most shared words), then the earliest
    result (Overpass returns them roughly nearest-first)."""
    best, best_score = None, 0
    for element in elements:
        tags = element.get("tags") or {}
        hours = tags.get("opening_hours")
        if not isinstance(hours, str) or not hours.strip():
            continue
        for tag in _NAME_TAGS:
            candidate = tags.get(tag)
            if isinstance(candidate, str) and names_match(item_name, candidate):
                score = len(significant_tokens(item_name) & significant_tokens(candidate))
                if score > best_score:
                    best, best_score = element, score
                break
    return best
