# Maps, search, geocoding and routing providers

TourWayva uses **Mapbox** as its main map, location search, geocoding and routing provider. **Google Places/Routes are optional and
off by default.** OpenCage and OpenRouteService (both OpenStreetMap-based) stay as fallbacks and as the source for kept lookups.

## Turn it on
1. Create two Mapbox tokens at account.mapbox.com:
   - a **public** token (`pk.…`), restricted to your site URLs -> frontend `VITE_MAPBOX_TOKEN`
   - a **server** token -> backend `MAPBOX_ACCESS_TOKEN` (if requests return 401/403, check the token's scopes)
2. Set both **together**. Mapbox's terms allow its geocoding and routing responses only alongside a Mapbox map.
3. Restart the backend and rebuild the frontend. Without the frontend token the OpenStreetMap map is shown.

## Who answers what
| Lookup | Order | Notes |
|---|---|---|
| Autocomplete (location picker) | Mapbox, OpenCage | Mapbox results are never cached or stored |
| Place/destination you keep | OpenCage (Mapbox first once `MAPBOX_GEOCODING_PERMANENT=true`) | kept answers need permanent geocoding |
| Named venue (restaurant, hotel) | OpenCage, then Mapbox (street addresses only) | Mapbox's geocoder returns no points of interest |
| Routes | Mapbox, OpenRouteService, Google Routes (if enabled) | Mapbox has no transit profile |

`GEOCODING_SUGGEST_PROVIDERS`, `GEOCODING_PROVIDERS`, `GEOCODING_VENUE_PROVIDERS` and `ROUTING_PROVIDERS` change the order.

## Data-use rules (enforced in `app/core/location_policy.py`, tested in `tests/unit/test_location_policy.py`)
- Mapbox **temporary** geocoding: display only, never cached, never saved. **Permanent** geocoding (`MAPBOX_GEOCODING_PERMANENT=true`,
  billed separately, card on file or contract) may be cached and saved. Autocomplete is always temporary.
- Mapbox **routes** are never saved. `MAPBOX_ROUTE_CACHE_SECONDS` (default 0) allows a short cache once you have checked your terms.
  Trip route distances from Mapbox are therefore recomputed on each view instead of stored.
- **Google** content is shown only on a Google map, so it is never drawn on the Mapbox map and a Google route path is withheld from it.
  Enable with `GOOGLE_PLACES_ENABLED` / `GOOGLE_ROUTES_ENABLED` (+ `GOOGLE_MAPS_API_KEY`, "Places API (New)" enabled, billing linked).
- OpenStreetMap-derived data (OpenCage, OpenRouteService, Overpass): cached and saved, with attribution.

Terms change. Re-check Mapbox's and Google's current terms before launch; this is a technical safeguard, not legal advice.

## Not done yet
- Hotel/attraction discovery without Google: with Google off, planning has no live venue source unless Amadeus is configured, and the
  planner marks its own suggestions as estimates. An OpenStreetMap-based discovery provider is the natural next step.
- Drawing route paths on the trip map (the map component supports `routes`, no screen passes them yet).
- Wiring `src/lib/mapConsent.js` to the cookie-consent store (Phase 3).
