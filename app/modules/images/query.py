"""Image search query clean-up (stdlib only). Place names from geocoders repeat themselves ("Dubai, Dubai, United Arab Emirates") and
over-specified queries find nothing. We try a few tidy variants that all KEEP the venue name: never a generic city photo standing in
for a named venue (that would show an unrelated picture as if it were the place)."""
from __future__ import annotations


def _clean_parts(entity: str) -> list[str]:
    seen, parts = set(), []
    for raw in entity.replace(" / ", ",").split(","):
        part = " ".join(raw.split())
        key = part.casefold()
        if part and key not in seen:
            seen.add(key)
            parts.append(part)
    return parts


def image_query_candidates(entity: str, *, limit: int = 3) -> list[str]:
    """Most specific first: the de-duplicated full name; the name with its place; the name alone."""
    parts = _clean_parts(entity)
    if not parts:
        return []
    candidates = [", ".join(parts)]
    if len(parts) > 2:
        candidates.append(f"{parts[0]} {parts[1]}")
    if len(parts) > 1:
        candidates.append(parts[0])
    out: list[str] = []
    for c in candidates:
        if c.casefold() not in {o.casefold() for o in out}:
            out.append(c)
    return out[:limit]


# What kind of place a name describes, from words in the name only. Used for an HONEST fallback photo: a generic restaurant picture is
# labelled "illustrative", never presented as that specific restaurant.
_CATEGORIES: tuple[tuple[frozenset, str], ...] = (
    (frozenset({"restaurant", "grill", "kitchen", "eatery", "bistro", "diner", "cafe", "café", "coffee", "bar", "tapas", "bakery", "canteen", "buka", "amala", "suya"}), "restaurant food"),
    (frozenset({"hotel", "resort", "inn", "lodge", "hostel", "villa", "suites", "suite", "apartments", "guesthouse"}), "hotel room"),
    (frozenset({"beach", "bay", "coast", "island", "lagoon"}), "tropical beach"),
    (frozenset({"museum", "gallery"}), "museum interior"),
    (frozenset({"market", "souk", "bazaar", "mall"}), "street market"),
    (frozenset({"mosque", "cathedral", "church", "temple", "shrine", "palace", "fort", "castle", "monument", "memorial", "ruins"}), "historic architecture"),
    (frozenset({"park", "garden", "gardens", "reserve", "forest", "waterfall", "falls", "mountain", "hill", "rock"}), "nature landscape"),
    (frozenset({"spa", "wellness", "massage"}), "spa wellness"),
    (frozenset({"club", "lounge", "nightclub"}), "nightlife"),
)


def category_hint(entity: str) -> str | None:
    words = {w.strip(".,()'\"").casefold() for w in entity.replace(",", " ").split()}
    for keywords, phrase in _CATEGORIES:
        if words & keywords:
            return phrase
    return None


def image_fallback_candidates(entity: str, *, limit: int = 3) -> list[tuple[str, str]]:
    """(search text, match level) to try AFTER the exact name found nothing, most relevant first. Match levels: 'category' (this kind of
    place, in the area) and 'area' (the neighbourhood or city). Never the venue's name, so nothing wrong is claimed."""
    parts = _clean_parts(entity)
    if not parts:
        return []
    category = category_hint(parts[0])
    area_near = " ".join(parts[1:3]) if len(parts) > 1 else ""
    area_wide = " ".join(parts[-2:]) if len(parts) > 2 else ""
    out: list[tuple[str, str]] = []
    if category and area_near:
        out.append((f"{category} {parts[1]}", "category"))
    if area_near:
        out.append((area_near, "area"))
    if area_wide and area_wide.casefold() != area_near.casefold():
        out.append((area_wide, "area"))
    if category:
        out.append((category, "category"))
    seen: set[str] = set()
    unique = [(q, m) for q, m in out if not (q.casefold() in seen or seen.add(q.casefold()))]
    return unique[:limit]
