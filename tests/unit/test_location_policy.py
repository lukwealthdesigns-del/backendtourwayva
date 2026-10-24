"""Location data-use policy (app/core/location_policy.py): what may be displayed, cached and stored per provider. Pure stdlib."""
from __future__ import annotations

from app.core import location_policy as p
from app.core.location_policy import MapEngine as E, Operation as O, Provider as P


def test_mapbox_temporary_geocoding_is_display_only_never_cached_or_stored():
    for op in (O.SUGGEST, O.FORWARD_GEOCODE, O.REVERSE_GEOCODE):
        use = p.data_use(P.MAPBOX, op)
        assert not use.may_cache and not use.may_store
        assert p.cache_ttl(P.MAPBOX, op, 3600) == 0


def test_mapbox_permanent_geocoding_may_be_cached_and_stored_but_autocomplete_never():
    assert p.may_store(P.MAPBOX, O.FORWARD_GEOCODE, mapbox_permanent=True)
    assert p.may_store(P.MAPBOX, O.REVERSE_GEOCODE, mapbox_permanent=True)
    assert p.cache_ttl(P.MAPBOX, O.FORWARD_GEOCODE, 3600, mapbox_permanent=True) == 3600
    assert not p.may_store(P.MAPBOX, O.SUGGEST, mapbox_permanent=True)
    assert p.cache_ttl(P.MAPBOX, O.SUGGEST, 3600, mapbox_permanent=True) == 0


def test_mapbox_routes_are_never_stored_and_cached_only_when_an_operator_opts_in():
    assert not p.may_store(P.MAPBOX, O.ROUTE, mapbox_route_cache_seconds=600)
    assert p.cache_ttl(P.MAPBOX, O.ROUTE, 7 * 86400) == 0
    assert p.cache_ttl(P.MAPBOX, O.ROUTE, 7 * 86400, mapbox_route_cache_seconds=300) == 300   # capped at the operator's ceiling
    assert p.cache_ttl(P.MAPBOX, O.ROUTE, 60, mapbox_route_cache_seconds=300) == 60


def test_mapbox_data_is_only_displayed_on_a_mapbox_map_and_google_data_only_on_a_google_map():
    assert p.may_display(P.MAPBOX, O.ROUTE, E.MAPBOX) and not p.may_display(P.MAPBOX, O.ROUTE, E.OSM)
    assert not p.may_display(P.MAPBOX, O.SUGGEST, E.GOOGLE)
    assert p.may_display(P.GOOGLE_PLACES, O.PLACE_DISCOVERY, E.GOOGLE)
    assert not p.may_display(P.GOOGLE_PLACES, O.PLACE_DISCOVERY, E.MAPBOX)
    assert not p.may_display(P.GOOGLE_ROUTES, O.ROUTE, E.MAPBOX)
    assert p.may_display("google_routes", "route", "google")   # plain strings work too


def test_google_caching_is_capped_at_thirty_days_and_nothing_is_stored():
    assert p.cache_ttl(P.GOOGLE_PLACES, O.PLACE_DISCOVERY, 90 * 86400) == p.GOOGLE_CACHE_CEILING_SECONDS == 30 * 86400
    assert not p.may_store(P.GOOGLE_PLACES, O.PLACE_DISCOVERY)


def test_open_data_providers_may_be_cached_stored_and_drawn_anywhere():
    for prov in (P.OPENCAGE, P.OPENROUTESERVICE, P.OVERPASS):
        use = p.data_use(prov, O.FORWARD_GEOCODE)
        assert use.may_cache and use.may_store and use.display_on == frozenset(E)
        assert "OpenStreetMap" in use.attribution
        assert p.cache_ttl(prov, O.FORWARD_GEOCODE, 1234) == 1234


def test_unknown_providers_get_the_strictest_rules_and_a_zero_ttl_is_never_raised():
    use = p.data_use("some_new_vendor", O.ROUTE)
    assert not use.may_cache and not use.may_store and not use.display_on
    assert p.cache_ttl(P.OPENCAGE, O.FORWARD_GEOCODE, 0) == 0


def test_attributions_are_distinct_and_ordered():
    assert p.attributions(["mapbox", "opencage", "mapbox", "openrouteservice", "nobody"]) == ["© Mapbox", p.OSM_ATTRIBUTION]
