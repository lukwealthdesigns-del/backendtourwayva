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
| Hotels and attractions | Google Places (if enabled), Amadeus (activities), then **OpenStreetMap** | OSM has no ratings, prices or availability: none are shown |

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

## Hotel and attraction discovery without Google (OpenStreetMap)
`app/providers/osm/` finds hotels and attractions near a destination through the Overpass API (keyless). Results are discovery only: a name,
location, type, website and address where OSM has them, ranked by how notable the place looks (Wikidata/Wikipedia link, tourism type, hotel
stars) and de-duplicated. OSM carries no ratings, prices or availability, so none are invented. Ids start with `osm:`.
It shares one courtesy budget with the opening-hours lookups (two queries in flight, a pause after 429/5xx, a circuit breaker) and caches
each ~1 km cell for 7 days. The public Overpass service is shared: for real traffic point `OVERPASS_API_URL` at your own instance.
If the main Overpass server cannot be reached or is overloaded, `OVERPASS_FALLBACK_URLS` (default: overpass.private.coffee) is tried next; public mirrors can lag the main database. Switch off with `OSM_DISCOVERY_ENABLED=false`. Data is (c) OpenStreetMap contributors (ODbL): keep the attribution visible.

## Not done yet
- Wiring the frontend's cookie-consent store into anything beyond maps (maps are already consent-gated).
- A test that Mapbox routes are not saved by `extras_service` (needs a database session to run).
- Yoruba/Hausa/Igbo, the admin console and the destination guides are not translated (see the frontend notes).

## Development without Redis or a worker
`PLANNING_EXECUTION` unset now means `task` outside staging/production: itinerary generation runs as a background task inside the API process, the request
returns at once and the page polls real progress. This needs ONE server process (plain `uvicorn`, no `--workers N`), because without Redis the job state is held in
memory. Production keeps using Celery (`background`). A second Generate click while one is running attaches to the running job instead of failing.

## Currency rates when CurrencyAPI has no valid key
A `401 invalid_api_key` from CurrencyAPI makes `/currency/rates` return 503. Fix the key (`CURRENCY_API_KEY`). For development you can opt in to the keyless
ExchangeRate-API open endpoint with `CURRENCY_FALLBACK_PROVIDER=open_er_api` (daily rates; attribution "Rates By Exchange Rate API" is required; not for a public
product: use a keyed plan).
