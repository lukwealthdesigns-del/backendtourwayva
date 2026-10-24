"""
Pure parsing of Mapbox Geocoding v6 and Directions v5 responses (no network, no web stack: unit-tested on its own).

Shapes follow Mapbox's documentation (docs.mapbox.com/api/search/geocoding, docs.mapbox.com/api/navigation/directions).
Everything is read defensively: Mapbox may add properties, and a missing field must degrade to "skip this result", never to a
guessed coordinate (TourWayva never fabricates a location).
"""
from __future__ import annotations

from typing import Any, Optional

from app.core.exceptions import NotFoundError, ProviderUnavailableError
from app.providers.geocoding.interface import GeocodeResult

# Place-level feature types worth offering in a travel location picker ("street"/"address"/"postcode" are noise there).
PLACE_FEATURE_TYPES = frozenset({"country", "region", "district", "place", "locality", "neighborhood"})
# A specific venue lookup needs a street address; Mapbox Geocoding v6 returns no points of interest, so anything coarser than
# an address would put a venue at a city centre. Those are skipped so another provider can answer.
VENUE_FEATURE_TYPES = frozenset({"address"})
VENUE_MIN_CONFIDENCE = frozenset({"exact", "high", "medium"})
_CONFIDENCE_SCORE = {"exact": 10, "high": 8, "medium": 6, "low": 3}


def _ctx_name(context: dict, key: str) -> Optional[str]:
    item = context.get(key)
    return item.get("name") if isinstance(item, dict) and item.get("name") else None


def _coordinates(feature: dict) -> Optional[tuple[float, float]]:
    props = feature.get("properties") or {}
    c = props.get("coordinates") or {}
    lat, lon = c.get("latitude"), c.get("longitude")
    if lat is None or lon is None:
        geom = (feature.get("geometry") or {}).get("coordinates") or []
        if len(geom) >= 2:
            lon, lat = geom[0], geom[1]
    try:
        lat, lon = float(lat), float(lon)
    except (TypeError, ValueError):
        return None
    if not (-90.0 <= lat <= 90.0 and -180.0 <= lon <= 180.0):
        return None
    return lat, lon


def _formatted(props: dict) -> str:
    full = (props.get("full_address") or "").strip()
    if full:
        return full
    head = (props.get("name_preferred") or props.get("name") or "").strip()
    tail = (props.get("place_formatted") or "").strip()
    return ", ".join(part for part in (head, tail) if part)


def parse_geocode_features(payload: dict, limit: int, *, precise: bool = False, any_type: bool = False) -> list[GeocodeResult]:
    """Distinct GeocodeResults from a v6 FeatureCollection. precise=False keeps place-level features (for pickers and
    destinations); precise=True keeps only confident street addresses (for a specific venue or address); any_type=True
    keeps every feature (reverse geocoding: Mapbox lists the most specific feature first)."""
    wanted = VENUE_FEATURE_TYPES if precise else PLACE_FEATURE_TYPES
    picked: list[GeocodeResult] = []
    seen: set[str] = set()
    for feature in (payload or {}).get("features") or []:
        props = feature.get("properties") or {}
        ftype = props.get("feature_type")
        if not any_type and ftype not in wanted:
            continue
        confidence = (props.get("match_code") or {}).get("confidence")
        if precise and confidence not in VENUE_MIN_CONFIDENCE:
            continue
        where = _coordinates(feature)
        formatted = _formatted(props)
        if where is None or not formatted:
            continue
        key = formatted.lower()
        if key in seen:
            continue
        seen.add(key)
        context = props.get("context") or {}
        country = context.get("country") or {}
        picked.append(GeocodeResult(
            formatted_address=formatted,
            latitude=where[0],
            longitude=where[1],
            country=(country.get("country_code") or "").upper() or None,
            city=_ctx_name(context, "place") or (props.get("name") if ftype == "place" else None),
            region=_ctx_name(context, "region"),
            provider="mapbox",
            confidence=_CONFIDENCE_SCORE.get(confidence),
        ))
        if len(picked) >= limit:
            break
    return picked


def parse_directions(payload: dict) -> tuple[float, float, Optional[str]]:
    """(distance_meters, duration_seconds, encoded_polyline) of the best route.
    'No route' answers raise NotFoundError (an answer, not an outage); anything unexpected raises ProviderUnavailableError."""
    code = (payload or {}).get("code")
    if code in ("NoRoute", "NoSegment"):
        raise NotFoundError("No route could be found between these locations.")
    if code not in (None, "Ok"):
        raise ProviderUnavailableError("Route calculation failed. Please try again.")
    routes = (payload or {}).get("routes") or []
    if not routes:
        raise NotFoundError("No route could be found between these locations.")
    best: Any = routes[0]
    try:
        distance, duration = float(best["distance"]), float(best["duration"])
    except (KeyError, TypeError, ValueError) as exc:
        raise ProviderUnavailableError("Route calculation failed. Please try again.") from exc
    geometry = best.get("geometry")
    return distance, duration, geometry if isinstance(geometry, str) and geometry else None
