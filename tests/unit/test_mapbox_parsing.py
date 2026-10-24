"""Mapbox Geocoding v6 / Directions v5 parsing (app/providers/mapbox/parsing.py). Payloads follow Mapbox's documented shapes."""
from __future__ import annotations

from app.core.exceptions import NotFoundError, ProviderUnavailableError
from app.providers.mapbox.parsing import parse_directions, parse_geocode_features


def _feature(name, ftype, lon, lat, *, formatted="", context=None, confidence=None, accuracy=None, full=None):
    props = {"mapbox_id": name, "feature_type": ftype, "name": name, "place_formatted": formatted,
             "coordinates": {"longitude": lon, "latitude": lat}, "context": context or {}}
    if full:
        props["full_address"] = full
    if confidence:
        props["match_code"] = {"confidence": confidence}
    if accuracy:
        props["coordinates"]["accuracy"] = accuracy
    return {"type": "Feature", "id": name, "geometry": {"type": "Point", "coordinates": [lon, lat]}, "properties": props}


IBADAN = _feature("Ibadan", "place", 3.9470, 7.3775, formatted="Oyo, Nigeria",
                  context={"region": {"name": "Oyo"}, "country": {"name": "Nigeria", "country_code": "NG"}})
STREET = _feature("1600 Pennsylvania Avenue Northwest", "address", -77.03655, 38.89768, formatted="Washington, District of Columbia 20500, United States",
                  confidence="exact", accuracy="rooftop",
                  context={"place": {"name": "Washington"}, "region": {"name": "District of Columbia"}, "country": {"name": "United States", "country_code": "US"}})


def test_place_results_become_geocode_results_with_country_city_and_region():
    out = parse_geocode_features({"features": [IBADAN]}, 5)
    assert len(out) == 1
    r = out[0]
    assert (r.formatted_address, r.latitude, r.longitude) == ("Ibadan, Oyo, Nigeria", 7.3775, 3.9470)
    assert (r.country, r.city, r.region, r.provider) == ("NG", "Ibadan", "Oyo", "mapbox")


def test_full_address_wins_when_present():
    f = _feature("Lagos", "place", 3.4, 6.5, formatted="Lagos, Nigeria", full="Lagos, Lagos State, Nigeria")
    assert parse_geocode_features({"features": [f]}, 5)[0].formatted_address == "Lagos, Lagos State, Nigeria"


def test_place_mode_skips_streets_addresses_and_postcodes_and_dedupes_and_respects_the_limit():
    feats = [STREET, _feature("20500", "postcode", 0, 0, formatted="US"), IBADAN, IBADAN,
             _feature("Ibadan North", "locality", 3.9, 7.4, formatted="Oyo, Nigeria"),
             _feature("Bodija", "neighborhood", 3.91, 7.43, formatted="Ibadan, Oyo, Nigeria")]
    names = [r.formatted_address for r in parse_geocode_features({"features": feats}, 2)]
    assert names == ["Ibadan, Oyo, Nigeria", "Ibadan North, Oyo, Nigeria"]


def test_precise_mode_accepts_only_confident_addresses_so_a_venue_is_never_pinned_to_a_city_centre():
    assert parse_geocode_features({"features": [IBADAN]}, 1, precise=True) == []
    assert len(parse_geocode_features({"features": [STREET]}, 1, precise=True)) == 1
    low = _feature("974 2100 South", "address", -111.8, 40.7, formatted="Salt Lake City, Utah, United States", confidence="low")
    assert parse_geocode_features({"features": [low]}, 1, precise=True) == []
    unscored = _feature("Somewhere", "address", 1.0, 2.0, formatted="Somewhere")
    assert parse_geocode_features({"features": [unscored]}, 1, precise=True) == []


def test_reverse_mode_keeps_the_most_specific_feature_of_any_type():
    out = parse_geocode_features({"features": [STREET, IBADAN]}, 1, any_type=True)
    assert out[0].formatted_address.startswith("1600 Pennsylvania")


def test_features_without_usable_coordinates_or_text_are_skipped_never_guessed():
    no_coords = {"type": "Feature", "geometry": {}, "properties": {"feature_type": "place", "name": "X", "place_formatted": "Y"}}
    bad_range = _feature("Nowhere", "place", 500, 200, formatted="Mars")
    nameless = _feature("", "place", 1, 2, formatted="")
    assert parse_geocode_features({"features": [no_coords, bad_range, nameless]}, 5) == []
    assert parse_geocode_features({}, 5) == [] and parse_geocode_features(None, 5) == []


def test_coordinates_fall_back_to_the_geometry_when_the_properties_omit_them():
    f = _feature("Accra", "place", 0, 0, formatted="Ghana")
    f["properties"].pop("coordinates")
    f["geometry"]["coordinates"] = [-0.2, 5.6]
    r = parse_geocode_features({"features": [f]}, 1)[0]
    assert (r.latitude, r.longitude) == (5.6, -0.2)


def test_directions_returns_distance_duration_and_geometry():
    d, t, g = parse_directions({"code": "Ok", "routes": [{"distance": 1234.5, "duration": 321.0, "geometry": "_p~iF~ps|U"}]})
    assert (d, t, g) == (1234.5, 321.0, "_p~iF~ps|U")
    assert parse_directions({"code": "Ok", "routes": [{"distance": 1, "duration": 2}]})[2] is None


def test_directions_no_route_is_an_answer_and_anything_else_is_an_outage():
    for payload in ({"code": "NoRoute"}, {"code": "NoSegment"}, {"code": "Ok", "routes": []}, {}):
        try:
            parse_directions(payload)
        except NotFoundError:
            continue
        raise AssertionError(f"expected NotFoundError for {payload}")
    for payload in ({"code": "InvalidInput"}, {"code": "Ok", "routes": [{"distance": "far"}]}):
        try:
            parse_directions(payload)
        except ProviderUnavailableError:
            continue
        raise AssertionError(f"expected ProviderUnavailableError for {payload}")
