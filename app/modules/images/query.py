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
