"""
Location data-use policy: what each provider's data may be DISPLAYED on, CACHED as and STORED as.  (stdlib only)

Map, geocoding, routing and place providers license their data differently, and mixing them carelessly is how an app ends up
with a perfectly working feature that a vendor can switch off. This module is the one place those rules live, so the services
that cache or persist provider data ask here instead of guessing, and a test can prove the answers.

Sources (checked against the vendors' own documentation, October 2026 - terms change, so re-check before a major release):
  * Mapbox Geocoding: TEMPORARY results (the default) must not be cached or stored; `permanent=true` results may be cached and
    stored, are billed separately and need a card on file or a contract. Responses may only be used together with a Mapbox map.
  * Mapbox Directions: no published right to store results, so TourWayva never stores them; caching is off unless an operator
    sets MAPBOX_ROUTE_CACHE_SECONDS after confirming their own terms.
  * Google Maps Platform (Places/Routes): results shown on a map must be shown on a Google map; place content may only be cached
    briefly (this module uses 30 days as the ceiling) and place IDs are the thing to keep.
  * OpenStreetMap-derived data (OpenCage, OpenRouteService, Overpass): open licence (ODbL) with attribution; cache and store.

This is a technical safeguard, not legal advice.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Iterable, Optional


class Provider(str, Enum):
    MAPBOX = "mapbox"
    GOOGLE_PLACES = "google_places"
    GOOGLE_ROUTES = "google_routes"
    OPENCAGE = "opencage"
    OPENROUTESERVICE = "openrouteservice"
    OVERPASS = "overpass"


class Operation(str, Enum):
    SUGGEST = "suggest"                  # autocomplete while typing
    FORWARD_GEOCODE = "forward_geocode"  # text -> coordinates (callers usually keep the answer)
    REVERSE_GEOCODE = "reverse_geocode"  # coordinates -> place (callers usually keep the answer)
    ROUTE = "route"
    PLACE_DISCOVERY = "place_discovery"  # hotels, attractions, restaurants


class MapEngine(str, Enum):
    MAPBOX = "mapbox"
    OSM = "osm"      # Leaflet + OpenStreetMap tiles (the fallback map)
    GOOGLE = "google"


_ALL_ENGINES = frozenset(MapEngine)
_ONLY_MAPBOX = frozenset({MapEngine.MAPBOX})
_ONLY_GOOGLE = frozenset({MapEngine.GOOGLE})

GOOGLE_CACHE_CEILING_SECONDS = 30 * 24 * 3600
OSM_ATTRIBUTION = "© OpenStreetMap contributors"


@dataclass(frozen=True)
class DataUse:
    provider: str
    operation: str
    display_on: frozenset        # which map engines may draw this data
    may_cache: bool              # short-lived copies (Redis) to save calls
    may_store: bool              # keep in our database (itineraries, saved places, durable cache)
    max_cache_seconds: Optional[int]   # None = no provider ceiling
    attribution: Optional[str]
    note: str = ""


def data_use(provider, operation, *, mapbox_permanent: bool = False, mapbox_route_cache_seconds: int = 0) -> DataUse:
    """The rules for one provider+operation. Unknown providers get the strictest answer (display nowhere, cache nothing)."""
    p = provider.value if isinstance(provider, Provider) else str(provider)
    op = operation.value if isinstance(operation, Operation) else str(operation)

    if p == Provider.MAPBOX.value:
        if op in (Operation.FORWARD_GEOCODE.value, Operation.REVERSE_GEOCODE.value):
            return DataUse(p, op, _ONLY_MAPBOX, mapbox_permanent, mapbox_permanent, None, "© Mapbox",
                           "permanent=true results may be stored" if mapbox_permanent else "temporary results: never cache or store")
        if op == Operation.SUGGEST.value:   # autocomplete is always temporary: nobody stores a half-typed query's results
            return DataUse(p, op, _ONLY_MAPBOX, False, False, None, "© Mapbox", "temporary results: never cache or store")
        if op == Operation.ROUTE.value:
            ttl = max(0, int(mapbox_route_cache_seconds))
            return DataUse(p, op, _ONLY_MAPBOX, ttl > 0, False, ttl or None, "© Mapbox",
                           "no right to store routes; short cache only if the operator enabled it")
    elif p in (Provider.GOOGLE_PLACES.value, Provider.GOOGLE_ROUTES.value):
        return DataUse(p, op, _ONLY_GOOGLE, True, False, GOOGLE_CACHE_CEILING_SECONDS, "Google Maps",
                       "show on a Google map only; keep place IDs, not coordinates/content")
    elif p in (Provider.OPENCAGE.value, Provider.OPENROUTESERVICE.value, Provider.OVERPASS.value):
        return DataUse(p, op, _ALL_ENGINES, True, True, None, OSM_ATTRIBUTION, "open data (ODbL): cache and store with attribution")
    return DataUse(p, op, frozenset(), False, False, None, None, "unknown provider: strictest rules")


def may_display(provider, operation, engine, **ctx) -> bool:
    engine = engine if isinstance(engine, MapEngine) else MapEngine(str(engine))
    return engine in data_use(provider, operation, **ctx).display_on


def cache_ttl(provider, operation, requested_seconds: int, **ctx) -> int:
    """How long a cached copy may live: 0 when caching is not allowed, else the request capped at the provider's ceiling."""
    use = data_use(provider, operation, **ctx)
    if not use.may_cache or requested_seconds <= 0:
        return 0
    return min(requested_seconds, use.max_cache_seconds) if use.max_cache_seconds else requested_seconds


def may_store(provider, operation, **ctx) -> bool:
    return data_use(provider, operation, **ctx).may_store


def attributions(providers: Iterable[str]) -> list[str]:
    """Distinct attribution lines for the providers behind a screen, in first-seen order."""
    seen: list[str] = []
    for name in providers:
        text = data_use(name, Operation.ROUTE).attribution
        if text and text not in seen:
            seen.append(text)
    return seen
