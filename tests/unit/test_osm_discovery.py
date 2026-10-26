"""OpenStreetMap discovery (app/providers/osm): queries, parsing, ranking, the provider's behaviour under failure, hotel-id routing and
default provider order. No network, no Redis."""
from __future__ import annotations

import asyncio
from types import SimpleNamespace as NS

import httpx

from app.core.config import settings
from app.core.exceptions import ProviderUnavailableError
from app.core.resilience import reset_breakers
from app.modules.hotels import service as hotel_service
from app.providers.opening_hours import overpass_provider as shared
from app.providers.osm import discovery as d
from app.providers.osm import overpass_discovery as osm
from app.providers.travel import registry
from app.providers.travel.resolver import Capability


def _run(coro):
    return asyncio.run(coro)


def _el(kind, id_, name, lat=6.5, lon=3.4, **tags):
    base = {"type": kind, "id": id_, "tags": {"name": name, **tags}}
    if kind == "node":
        base.update(lat=lat, lon=lon)
    else:
        base["center"] = {"lat": lat, "lon": lon}
    return base


# ---- pure logic ---------------------------------------------------------------------------------------------------------------

def test_queries_interpolate_only_numbers_and_fixed_type_lists():
    q = d.hotel_query(6.5244, 3.3792, 8000, 20)
    assert q.startswith("[out:json][timeout:20];") and "(around:8000,6.524400,3.379200)" in q and q.endswith("out tags center qt 150;")
    assert '["tourism"~"^(hotel|guest_house|hostel|motel|apartment|resort|chalet)$"]["name"]' in q
    a = d.attraction_query(6.5, 3.4, 5000, 20)
    assert a.count("nwr(around:5000,") == 4 and a.endswith(");out tags center qt 200;")
    assert d.lookup_query("node/12345", 20) == "[out:json][timeout:20];node(12345);out tags center;"
    assert d.lookup_query("relation/9", 20).startswith("[out:json][timeout:20];relation(9);")


def test_a_reference_that_is_not_node_way_or_relation_plus_digits_never_becomes_a_query():
    for bad in ("node/1);out;", "node/", "foo/1", "node/12a", "", "node/1 ", "way/ 1", None):
        assert d.lookup_query(bad, 20) is None and not d.valid_reference(bad)
    assert d.valid_reference("way/987654")


def test_parse_reads_nodes_and_way_centres_prefers_english_names_and_skips_unusable_elements():
    payload = {"elements": [
        _el("node", 1, "Hôtel Local", **{"name:en": "Local Hotel", "tourism": "hotel", "stars": "4", "website": "https://example.com", "addr:street": "Marina", "addr:housenumber": "5", "addr:city": "Lagos"}),
        _el("way", 2, "Big Resort", lat=6.6, lon=3.5, tourism="resort"),
        {"type": "node", "id": 3, "lat": 6.5, "lon": 3.4, "tags": {"tourism": "hotel"}},                 # no name
        {"type": "way", "id": 4, "tags": {"name": "No centre", "tourism": "hotel"}},                      # no coordinates
        _el("node", 5, "Off the map", lat=95, lon=3.4, tourism="hotel"),                                  # impossible latitude
        {"type": "area", "id": 6, "lat": 1, "lon": 1, "tags": {"name": "wrong type"}},
        "junk",
    ]}
    got = d.parse_elements(payload, hotel=True)
    assert [p["ref"] for p in got] == ["node/1", "way/2"]
    first = got[0]
    assert first["name"] == "Local Hotel" and first["label"] == "Hotel" and first["stars"] == "4"
    assert first["address"] == "5 Marina, Lagos" and first["website"] == "https://example.com"
    assert got[1]["latitude"] == 6.6 and got[1]["longitude"] == 3.5
    assert d.parse_elements({}, hotel=False) == [] and d.parse_elements(None, hotel=False) == []


def test_websites_with_other_schemes_are_dropped():
    for url in ("javascript:alert(1)", "ftp://x.org", "data:text/html,hi", "example.com"):
        got = d.parse_elements({"elements": [_el("node", 1, "X", tourism="museum", website=url)]}, hotel=False)
        assert got[0]["website"] is None
    got = d.parse_elements({"elements": [_el("node", 1, "X", tourism="museum", **{"contact:website": "HTTP://Example.com"})]}, hotel=False)
    assert got[0]["website"] == "HTTP://Example.com"


def test_prominence_ranks_a_wikidata_museum_above_an_unlinked_park():
    museum = d.parse_elements({"elements": [_el("node", 1, "National Museum", tourism="museum", wikidata="Q1", website="https://m.org")]}, hotel=False)[0]
    park = d.parse_elements({"elements": [_el("node", 2, "Corner Park", leisure="park")]}, hotel=False)[0]
    assert museum["score"] > park["score"]
    five = d.parse_elements({"elements": [_el("node", 3, "Grand", tourism="hotel", stars="5")]}, hotel=True)[0]
    none = d.parse_elements({"elements": [_el("node", 4, "Basic", tourism="hotel")]}, hotel=True)[0]
    assert five["score"] == 5 and none["score"] == 0
    weird = d.parse_elements({"elements": [_el("node", 5, "Odd", tourism="hotel", stars="lots")]}, hotel=True)[0]
    assert weird["score"] == 0


def test_rank_and_dedupe_keeps_the_best_copy_of_the_same_place_but_not_distant_namesakes():
    near = _el("node", 1, "Grand Hotel", lat=6.5000, lon=3.4000, tourism="hotel", stars="2")
    twin = _el("way", 2, "grand  hotel!", lat=6.5004, lon=3.4004, tourism="hotel", stars="5")        # about 60 m away, same name
    far = _el("node", 3, "Grand Hotel", lat=6.7, lon=3.6, tourism="hotel")                           # a different city's namesake
    other = _el("node", 4, "Plaza", lat=6.5001, lon=3.4001, tourism="hotel", stars="3")
    places = d.parse_elements({"elements": [near, twin, far, other]}, hotel=True)
    kept = d.rank_and_dedupe(places, 6.5, 3.4, limit=10)
    assert [p["ref"] for p in kept] == ["way/2", "node/4", "node/3"]       # best twin kept, namesake far away kept
    assert len(d.rank_and_dedupe(places, 6.5, 3.4, limit=2)) == 2


def test_a_200_reply_that_says_the_query_timed_out_counts_as_no_answer():
    assert d.overpass_gave_up({"elements": [], "remark": "runtime error: Query timed out in \"query\" at line 2 after 21 seconds."})
    assert d.overpass_gave_up({"remark": "runtime error: Query run out of memory"})
    assert not d.overpass_gave_up({"elements": []}) and not d.overpass_gave_up({"remark": "Some harmless note"}) and not d.overpass_gave_up(None)


# ---- the provider -------------------------------------------------------------------------------------------------------------

class _Resp:
    def __init__(self, status=200, payload=None, headers=None):
        self.status_code, self._payload, self.headers = status, payload if payload is not None else {"elements": []}, headers or {}
        self.request = NS(url="https://overpass.test")

    def json(self):
        return self._payload


class Env:
    def __init__(self, monkeypatch, responses):
        self.calls, self.responses, self.cache = [], list(responses), {}
        env = self

        class FakeClient:
            def __init__(self, *a, **k):
                self.headers = k.get("headers")

            async def __aenter__(self):
                return self

            async def __aexit__(self, *a):
                return False

            async def post(self, url, data=None):
                env.calls.append((url, data["data"], self.headers))
                outcome = env.responses.pop(0) if env.responses else _Resp()
                if isinstance(outcome, Exception):
                    raise outcome
                return outcome

        class FakeCache:
            @staticmethod
            async def get_json(key):
                return env.cache.get(key)

            @staticmethod
            async def set_json(key, value, ttl):
                env.cache[key] = value

        monkeypatch.setattr(settings, "OVERPASS_FALLBACK_URLS", "")        # tests that want a mirror set one explicitly
        monkeypatch.setattr(settings, "OVERPASS_API_URL", "https://main.test/api/interpreter")
        monkeypatch.setattr(osm.httpx, "AsyncClient", FakeClient)
        monkeypatch.setattr(osm, "CacheService", FakeCache)
        monkeypatch.setattr(shared, "_cooldown_until", 0.0)
        reset_breakers()


HOTELS = {"elements": [
    _el("node", 11, "Lagos Palace", lat=6.52, lon=3.38, tourism="hotel", stars="5", website="https://palace.example", wikidata="Q9"),
    _el("way", 12, "Budget Inn", lat=6.53, lon=3.39, tourism="guest_house"),
]}


def test_hotels_are_discovery_results_with_no_price_rating_or_invented_detail(monkeypatch):
    env = Env(monkeypatch, [_Resp(payload=HOTELS)])
    offers = _run(osm.OsmHotelDiscoveryProvider().discover_hotels(latitude=6.5244, longitude=3.3792, destination="Lagos"))
    assert [o.hotel_name for o in offers] == ["Lagos Palace", "Budget Inn"]
    top = offers[0]
    assert (top.kind, top.provider, top.hotel_id, top.provider_reference) == ("discovery", "osm", "osm:node/11", "node/11")
    assert top.price_total is None and top.currency is None and top.rating is None and top.user_rating_count is None and top.price_level is None
    assert top.maps_url == "https://www.openstreetmap.org/node/11" and top.website == "https://palace.example"
    assert top.room_description == "Hotel · 5-star"
    assert env.calls[0][2]["User-Agent"].startswith("TourWayva/")             # Overpass asks clients to identify themselves


def test_a_second_search_in_the_same_cell_is_served_from_cache_without_calling_overpass(monkeypatch):
    env = Env(monkeypatch, [_Resp(payload=HOTELS)])
    p = osm.OsmHotelDiscoveryProvider()
    _run(p.discover_hotels(latitude=6.5244, longitude=3.3792, destination="Lagos"))
    again = _run(p.discover_hotels(latitude=6.5201, longitude=3.3830, destination="Lagos"))       # a few hundred metres away: same 0.01-degree cell
    assert len(env.calls) == 1 and len(again) == 2


def test_an_empty_answer_is_a_real_answer_and_is_cached(monkeypatch):
    env = Env(monkeypatch, [_Resp(payload={"elements": []})])
    assert _run(osm.OsmActivityProvider().search_activities(latitude=-30.0, longitude=25.0, radius_km=5)) == []
    assert _run(osm.OsmActivityProvider().search_activities(latitude=-30.0, longitude=25.0, radius_km=5)) == [] and len(env.calls) == 1


def test_a_timeout_reported_inside_a_200_reply_is_an_outage_and_is_never_cached(monkeypatch):
    env = Env(monkeypatch, [_Resp(payload={"elements": [], "remark": "runtime error: Query timed out"})])
    try:
        _run(osm.OsmHotelDiscoveryProvider().discover_hotels(latitude=6.5, longitude=3.4, destination="Lagos"))
    except ProviderUnavailableError:
        assert env.cache == {}
        return
    raise AssertionError("expected ProviderUnavailableError")


def test_rate_limiting_raises_starts_the_shared_cooldown_and_stops_further_calls(monkeypatch):
    env = Env(monkeypatch, [_Resp(status=429, headers={"Retry-After": "45"})])
    p = osm.OsmActivityProvider()
    for _ in range(2):
        try:
            _run(p.search_activities(latitude=6.5, longitude=3.4, radius_km=5))
        except ProviderUnavailableError:
            continue
        raise AssertionError("expected ProviderUnavailableError")
    assert len(env.calls) == 1 and shared.cooling_down()      # the second attempt never reached the network
    assert env.cache == {}


def test_attractions_become_activity_results_without_prices_or_ratings(monkeypatch):
    payload = {"elements": [_el("node", 21, "National Museum", lat=6.45, lon=3.39, tourism="museum", wikidata="Q5", description="Art and history")]}
    Env(monkeypatch, [_Resp(payload=payload)])
    [a] = _run(osm.OsmActivityProvider().search_activities(latitude=6.45, longitude=3.39, radius_km=3))
    assert (a.activity_id, a.provider, a.name, a.description) == ("osm:node/21", "osm", "National Museum", "Art and history")
    assert a.price_amount is None and a.currency is None and a.rating is None and a.booking_link is None and a.picture_url is None
    assert a.maps_url == "https://www.openstreetmap.org/node/21"


def test_get_hotel_looks_one_place_up_and_never_queries_for_a_malformed_reference(monkeypatch):
    env = Env(monkeypatch, [_Resp(payload={"elements": [_el("node", 11, "Lagos Palace", tourism="hotel")]})])
    hotel = _run(osm.OsmHotelDiscoveryProvider().get_hotel(reference="node/11"))
    assert hotel.hotel_name == "Lagos Palace" and "node(11);" in env.calls[0][1]
    assert _run(osm.OsmHotelDiscoveryProvider().get_hotel(reference="node/1);out;")) is None and len(env.calls) == 1


# ---- wiring -------------------------------------------------------------------------------------------------------------------

def test_with_google_off_discovery_falls_to_amadeus_then_osm_and_hotels_use_osm(monkeypatch):
    for name, value in (("GOOGLE_MAPS_API_KEY", "key"), ("GOOGLE_PLACES_ENABLED", False), ("OSM_DISCOVERY_ENABLED", True),
                        ("AMADEUS_CLIENT_ID", ""), ("AMADEUS_CLIENT_SECRET", ""),
                        ("HOTEL_DISCOVERY_PROVIDERS", "google_places,osm"), ("ACTIVITY_PROVIDERS", "google_places,amadeus,osm")):
        monkeypatch.setattr(settings, name, value)
    registry.reset_resolver()
    names = lambda cap: [n for n, _ in registry.get_resolver().providers(cap)]   # noqa: E731
    assert names(Capability.HOTEL_DISCOVERY) == ["osm"] and names(Capability.ACTIVITY_DISCOVERY) == ["osm"]
    monkeypatch.setattr(settings, "GOOGLE_PLACES_ENABLED", True)
    registry.reset_resolver()
    assert names(Capability.HOTEL_DISCOVERY) == ["google_places", "osm"]
    monkeypatch.setattr(settings, "OSM_DISCOVERY_ENABLED", False)
    registry.reset_resolver()
    assert names(Capability.HOTEL_DISCOVERY) == ["google_places"]
    registry.reset_resolver()


def test_a_hotel_detail_lookup_goes_to_the_provider_that_made_the_id(monkeypatch):
    asked = []

    class Fake:
        def __init__(self, label):
            self.label = label

        async def get_hotel(self, *, reference):
            asked.append((self.label, reference))
            return None

    class FakeResolver:
        def providers(self, capability):
            return [("google_places", Fake("google")), ("osm", Fake("osm"))]

        def first(self, capability):
            raise AssertionError("must not pick 'whichever is first'")

    monkeypatch.setattr(hotel_service, "get_resolver", lambda: FakeResolver())
    svc = hotel_service.HotelService()
    for hid in ("osm:node/11", "gp:ChIJabc"):
        assert _run(svc.get_offer(hotel_id=hid, check_in=None, check_out=None, adults=2)) is None
    assert asked == [("osm", "node/11"), ("google", "ChIJabc")]


# ---- failover between Overpass servers ----------------------------------------------------------------------------------------

MIRROR = "https://mirror.test/api/interpreter"


def test_endpoints_are_the_configured_server_then_clean_unique_fallbacks(monkeypatch):
    Env(monkeypatch, [])
    monkeypatch.setattr(settings, "OVERPASS_FALLBACK_URLS", f" {MIRROR} , ftp://bad , https://main.test/api/interpreter,{MIRROR},, ")
    assert osm.endpoints() == ["https://main.test/api/interpreter", MIRROR]


def test_an_unreachable_main_server_fails_over_to_the_mirror_and_the_answer_is_used(monkeypatch):
    env = Env(monkeypatch, [httpx.ConnectError("All connection attempts failed"), _Resp(payload=HOTELS)])
    monkeypatch.setattr(settings, "OVERPASS_FALLBACK_URLS", MIRROR)
    offers = _run(osm.OsmHotelDiscoveryProvider().discover_hotels(latitude=6.5244, longitude=3.3792, destination="Lagos"))
    assert [o.hotel_name for o in offers] == ["Lagos Palace", "Budget Inn"]
    assert [c[0] for c in env.calls] == ["https://main.test/api/interpreter", MIRROR]
    assert not shared.cooling_down()                                    # one server answered: no reason to pause everyone


def test_a_busy_main_server_and_a_query_timeout_both_move_on_to_the_next_server(monkeypatch):
    for first in (_Resp(status=504), _Resp(status=429, headers={"Retry-After": "30"}), _Resp(payload={"elements": [], "remark": "runtime error: Query timed out"})):
        env = Env(monkeypatch, [first, _Resp(payload=HOTELS)])
        monkeypatch.setattr(settings, "OVERPASS_FALLBACK_URLS", MIRROR)
        offers = _run(osm.OsmHotelDiscoveryProvider().discover_hotels(latitude=6.5, longitude=3.4, destination="Lagos"))
        assert len(offers) == 2 and len(env.calls) == 2


def test_a_4xx_is_our_own_mistake_so_no_other_server_is_asked(monkeypatch):
    env = Env(monkeypatch, [_Resp(status=400)])
    monkeypatch.setattr(settings, "OVERPASS_FALLBACK_URLS", MIRROR)
    try:
        _run(osm.OsmHotelDiscoveryProvider().discover_hotels(latitude=6.5, longitude=3.4, destination="Lagos"))
    except ProviderUnavailableError:
        assert len(env.calls) == 1 and not shared.cooling_down()
        return
    raise AssertionError("expected ProviderUnavailableError")


def test_when_every_server_fails_it_is_an_outage_and_only_then_the_shared_cooldown_starts(monkeypatch):
    env = Env(monkeypatch, [httpx.ConnectError("down"), _Resp(status=429, headers={"Retry-After": "60"})])
    monkeypatch.setattr(settings, "OVERPASS_FALLBACK_URLS", MIRROR)
    try:
        _run(osm.OsmActivityProvider().search_activities(latitude=6.5, longitude=3.4, radius_km=5))
    except ProviderUnavailableError:
        assert len(env.calls) == 2 and shared.cooling_down() and env.cache == {}
        return
    raise AssertionError("expected ProviderUnavailableError")
