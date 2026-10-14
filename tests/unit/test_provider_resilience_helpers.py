"""Log redaction, Overpass politeness, impossible-route detection and image query clean-up (all pure)."""
import time

import pytest

from app.core.redaction import redact_text
from app.modules.images.query import image_query_candidates
from app.modules.maps.limits import ORS_NO_ROUTE_CODES, haversine_km, route_too_far, valid_coordinates
from app.providers.opening_hours.overpass_policy import cell_cache_key, cell_of, cooldown_seconds, retry_after_seconds, slim_elements


# ---- secrets never reach the logs ----
@pytest.mark.parametrize("line,secret", [
    ('HTTP Request: GET https://api.weatherapi.com/v1/current.json?q=1%2C2&key=abc123SECRET "HTTP/1.1 200 OK"', "abc123SECRET"),
    ("GET https://api.opencagedata.com/geocode/v1/json?q=x&limit=10&no_annotations=1&key=zzTOPSECRETzz", "zzTOPSECRETzz"),
    ("GET http://api.aviationstack.com/v1/flights?access_key=AVIA999&flight_iata=BA75", "AVIA999"),
    ("GET https://example.com/x?appid=APPID777&q=1", "APPID777"),
    ("Authorization: Bearer abcdefghijklmnop12345", "abcdefghijklmnop12345"),
])
def test_credentials_are_scrubbed_from_whole_log_lines(line, secret):
    out = redact_text(line)
    assert secret not in out and "***" in out


def test_harmless_text_is_untouched_and_never_truncated():
    line = "HTTP Request: GET https://api.example.com/v1/items?q=Paris&limit=10 " + "x" * 600
    assert redact_text(line) == line


# ---- Overpass ----
def test_nearby_venues_share_one_cell_and_one_cache_key():
    assert cell_cache_key(25.2048, 55.2709) == cell_cache_key(25.2052, 55.2711)           # inside the same 0.001-degree cell
    assert cell_cache_key(25.2048, 55.2709) != cell_cache_key(25.3048, 55.2709)           # 11 km apart
    assert cell_of(25.20481, 55.27092) == (25.205, 55.271)


def test_retry_after_numbers_dates_and_junk():
    assert retry_after_seconds("45") == 45.0
    assert retry_after_seconds(None) is None and retry_after_seconds("soon") is None
    future = time.strftime("%a, %d %b %Y %H:%M:%S GMT", time.gmtime(time.time() + 120))
    assert 100 <= retry_after_seconds(future) <= 125


def test_pause_after_a_refusal():
    assert cooldown_seconds(429) == 60.0
    assert cooldown_seconds(429, "20") == 20.0
    assert cooldown_seconds(429, "99999") == 300.0           # never an unbounded wait
    assert cooldown_seconds(504) == 30.0 and cooldown_seconds(503) == 30.0
    assert cooldown_seconds(400) == 0.0 and cooldown_seconds(200) == 0.0


def test_cached_cells_keep_only_what_matching_needs():
    out = slim_elements([
        {"id": 1, "lat": 1, "lon": 2, "tags": {"name": "Louvre", "opening_hours": "Mo-Su 09:00-18:00", "wheelchair": "yes", "phone": "123"}},
        {"id": 2, "tags": {"name": "No hours"}},
        "junk", {"id": 3, "tags": None},
    ])
    assert out == [{"tags": {"name": "Louvre", "opening_hours": "Mo-Su 09:00-18:00"}}]


# ---- routes ----
def test_known_distances():
    km = haversine_km(6.5244, 3.3792, 25.2048, 55.2708)             # Lagos to Dubai
    assert 5800 < km < 6200
    assert haversine_km(0, 0, 0, 0) == 0


@pytest.mark.parametrize("mode,km,too_far", [
    ("driving", 800, False), ("driving", 5400, True), ("walking", 12, False), ("walking", 300, True),
    ("cycling", 150, False), ("cycling", 2000, True), ("transit", 3000, False), ("transit", 6000, True),
])
def test_routes_that_cannot_exist_are_detected(mode, km, too_far):
    assert route_too_far(mode, km) is too_far


def test_coordinate_sanity_and_provider_codes():
    assert valid_coordinates(6.5, 3.4) and not valid_coordinates(91, 0) and not valid_coordinates(0, 181)
    assert 2004 in ORS_NO_ROUTE_CODES                                # the "route distance too long" error from the logs


# ---- images ----
def test_repeated_place_names_are_collapsed():
    assert image_query_candidates("Dubai Museum, Al Fahidi Fort, Dubai, Dubai, United Arab Emirates") == [
        "Dubai Museum, Al Fahidi Fort, Dubai, United Arab Emirates", "Dubai Museum Al Fahidi Fort", "Dubai Museum"]


def test_venue_name_is_always_kept_never_a_generic_city():
    for entity in ("Al Ustad Special Kabab, Dubai, Dubai, United Arab Emirates", "Gold Souk & Spice Souk, Deira, Dubai"):
        for c in image_query_candidates(entity):
            assert c.startswith(entity.split(",")[0].strip().split(" ")[0])
    assert image_query_candidates("Paris") == ["Paris"]
    assert image_query_candidates("  ") == []
