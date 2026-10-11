"""
Pure helpers for Google Places (New) / Routes responses and signed photo links. Stdlib only, so they are unit-tested
without the web stack.
"""
from __future__ import annotations

import hashlib
import hmac
import time
from typing import Any, Optional
from urllib.parse import quote

PLACES_BASE = "https://places.googleapis.com/v1"
ROUTES_URL = "https://routes.googleapis.com/directions/v2:computeRoutes"
PLACE_URL_TEMPLATE = "https://www.google.com/maps/place/?q=place_id:{place_id}"

# What we ask Places for. The field mask decides the billing SKU, so it lists only what we use.
PLACE_FIELDS = (
    "places.id,places.displayName,places.formattedAddress,places.location,places.rating,places.userRatingCount,"
    "places.photos,places.websiteUri,places.googleMapsUri,places.priceLevel,places.editorialSummary"
)

_PRICE_SYMBOL = {
    "PRICE_LEVEL_FREE": "Free", "PRICE_LEVEL_INEXPENSIVE": "$", "PRICE_LEVEL_MODERATE": "$$",
    "PRICE_LEVEL_EXPENSIVE": "$$$", "PRICE_LEVEL_VERY_EXPENSIVE": "$$$$",
}


def price_level_symbol(level: Optional[str]) -> Optional[str]:
    """Google's price LEVEL as $-$$$$. A qualitative hint, never an amount."""
    return _PRICE_SYMBOL.get(level or "")


def parse_place(raw: dict[str, Any]) -> Optional[dict[str, Any]]:
    """One Places (New) place -> our neutral dict, or None when it lacks an id, a name or coordinates."""
    place_id = raw.get("id")
    name = (raw.get("displayName") or {}).get("text")
    loc = raw.get("location") or {}
    lat, lon = loc.get("latitude"), loc.get("longitude")
    if not place_id or not name or lat is None or lon is None:
        return None
    photos = raw.get("photos") or []
    return {
        "place_id": place_id,
        "name": name,
        "address": raw.get("formattedAddress"),
        "latitude": float(lat),
        "longitude": float(lon),
        "rating": raw.get("rating"),
        "user_rating_count": raw.get("userRatingCount"),
        "photo_name": photos[0].get("name") if photos else None,
        "website": raw.get("websiteUri"),
        "maps_url": raw.get("googleMapsUri") or PLACE_URL_TEMPLATE.format(place_id=place_id),
        "price_level": price_level_symbol(raw.get("priceLevel")),
        "summary": (raw.get("editorialSummary") or {}).get("text"),
    }


def parse_places(data: dict[str, Any]) -> list[dict[str, Any]]:
    out, seen = [], set()
    for raw in data.get("places") or []:
        item = parse_place(raw)
        if item and item["place_id"] not in seen:
            seen.add(item["place_id"])
            out.append(item)
    return out


def parse_duration_seconds(value: Optional[str]) -> Optional[float]:
    """Routes durations arrive as strings like '1234s' (sometimes '1234.5s')."""
    if not value or not str(value).endswith("s"):
        return None
    try:
        return float(str(value)[:-1])
    except ValueError:
        return None


# ---- Signed photo links: the browser can load a photo but never sees our Google key. Links are stored in saved itineraries,
# so they live ~13 months; a link only ever serves the one photo it was issued for. ----
def _sig(secret: str, ref: str, exp: int) -> str:
    return hmac.new(secret.encode(), f"{ref}|{exp}".encode(), hashlib.sha256).hexdigest()[:32]


def sign_photo(secret: str, photo_name: str, *, ttl_seconds: int = 400 * 24 * 3600, now: Optional[float] = None) -> tuple[int, str]:
    exp = int((now if now is not None else time.time()) + ttl_seconds)
    return exp, _sig(secret, photo_name, exp)


def verify_photo(secret: str, photo_name: str, exp: int, sig: str, *, now: Optional[float] = None) -> bool:
    if not photo_name.startswith("places/") or "/photos/" not in photo_name or ".." in photo_name:
        return False
    if exp < (now if now is not None else time.time()):
        return False
    return hmac.compare_digest(_sig(secret, photo_name, exp), sig or "")


def photo_path(base_path: str, secret: str, photo_name: Optional[str], *, now: Optional[float] = None) -> Optional[str]:
    """`/api/v1/places/photo?ref=...&exp=...&sig=...` for a Places photo name, or None when there is no photo."""
    if not photo_name:
        return None
    exp, sig = sign_photo(secret, photo_name, now=now)
    return f"{base_path}?ref={quote(photo_name, safe='')}&exp={exp}&sig={sig}"
