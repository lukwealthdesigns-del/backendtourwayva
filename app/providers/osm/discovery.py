"""
Pure logic for OpenStreetMap place discovery (stdlib only, unit-tested without the web stack).

Builds the Overpass queries, turns raw OSM elements into plain place dicts, ranks them by prominence, and removes duplicates.
Nothing here invents data: OSM has no ratings, prices or availability, so none are produced. A place without a name or usable
coordinates is skipped, never guessed.
"""
from __future__ import annotations

import math
import re
import unicodedata
from typing import Any, Optional

HOTEL_TYPES = "hotel|guest_house|hostel|motel|apartment|resort|chalet"
TOURISM_ATTRACTIONS = "attraction|museum|gallery|viewpoint|zoo|aquarium|theme_park"
HISTORIC_ATTRACTIONS = "monument|castle|ruins|memorial|archaeological_site|fort|palace|city_gate|church|temple|tomb"
NATURAL_ATTRACTIONS = "beach|waterfall|peak|cave_entrance|hot_spring"
LEISURE_ATTRACTIONS = "park|garden|nature_reserve"

_OSM_REF = re.compile(r"^(node|way|relation)/(\d{1,15})$")
_LABELS = {
    "hotel": "Hotel", "guest_house": "Guest house", "hostel": "Hostel", "motel": "Motel", "apartment": "Apartment", "resort": "Resort",
    "chalet": "Chalet", "attraction": "Attraction", "museum": "Museum", "gallery": "Gallery", "viewpoint": "Viewpoint", "zoo": "Zoo",
    "aquarium": "Aquarium", "theme_park": "Theme park", "monument": "Monument", "castle": "Castle", "ruins": "Ruins", "memorial": "Memorial",
    "archaeological_site": "Archaeological site", "fort": "Fort", "palace": "Palace", "city_gate": "City gate", "church": "Church",
    "temple": "Temple", "tomb": "Tomb", "beach": "Beach", "waterfall": "Waterfall", "peak": "Peak", "cave_entrance": "Cave",
    "hot_spring": "Hot spring", "park": "Park", "garden": "Garden", "nature_reserve": "Nature reserve",
}


def valid_reference(reference: str) -> bool:
    return bool(_OSM_REF.match(reference or ""))


def _around(radius_m: int, lat: float, lon: float) -> str:
    return f"(around:{int(radius_m)},{lat:.6f},{lon:.6f})"


# Only numbers are interpolated; the type lists above are constants, so nothing a user typed ever reaches a query.
def hotel_query(lat: float, lon: float, radius_m: int, timeout_s: int, limit: int = 150) -> str:
    return (f"[out:json][timeout:{int(timeout_s)}];"
            f'nwr{_around(radius_m, lat, lon)}["tourism"~"^({HOTEL_TYPES})$"]["name"];out tags center qt {int(limit)};')


def attraction_query(lat: float, lon: float, radius_m: int, timeout_s: int, limit: int = 200) -> str:
    a = _around(radius_m, lat, lon)
    return (f"[out:json][timeout:{int(timeout_s)}];("
            f'nwr{a}["tourism"~"^({TOURISM_ATTRACTIONS})$"]["name"];'
            f'nwr{a}["historic"~"^({HISTORIC_ATTRACTIONS})$"]["name"];'
            f'nwr{a}["natural"~"^({NATURAL_ATTRACTIONS})$"]["name"];'
            f'nwr{a}["leisure"~"^({LEISURE_ATTRACTIONS})$"]["name"];'
            f");out tags center qt {int(limit)};")


def lookup_query(reference: str, timeout_s: int) -> Optional[str]:
    m = _OSM_REF.match(reference or "")
    return f"[out:json][timeout:{int(timeout_s)}];{m.group(1)}({m.group(2)});out tags center;" if m else None


def haversine_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    p1, p2 = math.radians(lat1), math.radians(lat2)
    a = math.sin((p2 - p1) / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(math.radians(lon2 - lon1) / 2) ** 2
    return 12742000.0 * math.asin(math.sqrt(a))


def normalize_name(name: str) -> str:
    s = unicodedata.normalize("NFKD", name).casefold()
    return re.sub(r"[^a-z0-9\u0400-\u04ff\u0600-\u06ff\u4e00-\u9fff]+", "", "".join(c for c in s if not unicodedata.combining(c)))


def _website(tags: dict) -> Optional[str]:
    url = (tags.get("website") or tags.get("contact:website") or "").strip()
    return url if re.match(r"^https?://", url, re.I) else None   # never pass javascript: or other schemes through


def _address(tags: dict) -> Optional[str]:
    if tags.get("addr:full"):
        return tags["addr:full"].strip() or None
    street = " ".join(x for x in (tags.get("addr:housenumber"), tags.get("addr:street")) if x)
    parts = [p for p in (street, tags.get("addr:suburb"), tags.get("addr:city") or tags.get("addr:town") or tags.get("addr:village")) if p]
    return ", ".join(parts) or None


def _kind(tags: dict) -> Optional[str]:
    for key in ("tourism", "historic", "natural", "leisure"):
        value = tags.get(key)
        if value in _LABELS:
            return value
    return None


def prominence(tags: dict, *, hotel: bool) -> int:
    """How notable a place looks from its OSM tags alone (a ranking hint, never shown as a rating)."""
    score = 0
    if tags.get("wikidata") or tags.get("wikipedia"):
        score += 4
    if tags.get("website") or tags.get("contact:website"):
        score += 1
    if tags.get("name:en"):
        score += 1
    if tags.get("brand"):
        score += 1
    if hotel:
        try:
            score += max(0, min(5, int(float(str(tags.get("stars", "0")).split(";")[0].replace("S", "")))))
        except ValueError:
            pass
        return score
    kind = _kind(tags)
    score += 2 if kind in ("attraction", "museum", "theme_park", "zoo", "aquarium") else 1 if kind in ("gallery", "viewpoint") else 0
    score += 1 if tags.get("heritage") else 0
    score += 1 if tags.get("opening_hours") else 0
    score += 1 if tags.get("image") or tags.get("wikimedia_commons") else 0
    return score


def parse_elements(payload: Any, *, hotel: bool) -> list[dict[str, Any]]:
    """Plain place dicts from an Overpass response (unsorted, may contain duplicates)."""
    out: list[dict[str, Any]] = []
    for e in (payload or {}).get("elements") or []:
        if not isinstance(e, dict) or e.get("type") not in ("node", "way", "relation") or not isinstance(e.get("id"), int):
            continue
        tags = e.get("tags") if isinstance(e.get("tags"), dict) else {}
        name = (tags.get("name:en") or tags.get("name") or "").strip()
        point = e if "lat" in e else (e.get("center") or {})
        try:
            lat, lon = float(point["lat"]), float(point["lon"])
        except (KeyError, TypeError, ValueError):
            continue
        if not name or not (-90 <= lat <= 90 and -180 <= lon <= 180):
            continue
        kind = _kind(tags)
        description = (tags.get("description:en") or tags.get("description") or "").strip()[:200] or None
        out.append({
            "ref": f"{e['type']}/{e['id']}", "name": name, "latitude": lat, "longitude": lon, "kind": kind,
            "label": _LABELS.get(kind or "", None), "description": description, "address": _address(tags), "website": _website(tags),
            "stars": tags.get("stars") if hotel else None, "score": prominence(tags, hotel=hotel),
        })
    return out


def rank_and_dedupe(places: list[dict[str, Any]], lat: float, lon: float, limit: int, *, same_place_m: float = 150.0) -> list[dict[str, Any]]:
    """Most prominent first (nearer wins a tie); the same name within `same_place_m` metres is one place."""
    ordered = sorted(places, key=lambda p: (-p["score"], haversine_m(lat, lon, p["latitude"], p["longitude"]), p["name"]))
    kept: list[dict[str, Any]] = []
    for p in ordered:
        key = normalize_name(p["name"])
        if key and any(normalize_name(k["name"]) == key and haversine_m(k["latitude"], k["longitude"], p["latitude"], p["longitude"]) < same_place_m for k in kept):
            continue
        kept.append(p)
        if len(kept) >= limit:
            break
    return kept


def overpass_gave_up(payload: Any) -> bool:
    """Overpass can answer 200 OK with an empty list and a remark that the query timed out: that is 'could not answer', not 'no venues'."""
    remark = str((payload or {}).get("remark") or "").lower()
    return "runtime error" in remark or "timed out" in remark or "out of memory" in remark
