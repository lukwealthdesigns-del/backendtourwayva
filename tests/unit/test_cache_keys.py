from app.services.cache_service import (
    currency_rate_cache_key,
    geocode_cache_key,
    reverse_geocode_cache_key,
    weather_current_cache_key,
)


def test_geocode_key_deterministic():
    assert geocode_cache_key("paris france") == "geocode:paris france"


def test_currency_key_uppercases():
    assert currency_rate_cache_key("usd", "ngn") == "currency:USD:NGN"


def test_reverse_geocode_key_rounds_coordinates():
    key1 = reverse_geocode_cache_key(6.5243793, 3.3792057)
    key2 = reverse_geocode_cache_key(6.52438, 3.37921)  # same location, tiny float noise
    assert key1 == key2


def test_weather_current_key_rounds_coordinates():
    key1 = weather_current_cache_key(51.5074, -0.1278)
    key2 = weather_current_cache_key(51.50741, -0.12781)
    assert key1 == key2


def test_image_key_normalizes_whitespace_and_case():
    from app.services.cache_service import image_cache_key

    assert image_cache_key("  Eiffel   Tower ", "EN") == "image:eiffel_tower:en"


def test_route_key_deterministic_per_mode():
    from app.services.cache_service import route_cache_key

    key_walk = route_cache_key(6.5244, 3.3792, 6.4550, 3.3841, "walking")
    key_drive = route_cache_key(6.5244, 3.3792, 6.4550, 3.3841, "driving")
    assert key_walk != key_drive
    assert key_walk.startswith("route:walking:")


def test_hotel_search_key_uppercases_city():
    from app.services.cache_service import hotel_search_cache_key

    assert hotel_search_cache_key("par", "2026-10-01", "2026-10-05", 2) == "hotel:PAR:2026-10-01:2026-10-05:2"


def test_flight_search_key_handles_oneway():
    from app.services.cache_service import flight_search_cache_key

    key = flight_search_cache_key("los", "lhr", "2026-11-01", None, 1, None)
    assert key == "flight:LOS:LHR:2026-11-01:oneway:1:any"


def test_flight_search_key_distinguishes_cabin():
    from app.services.cache_service import flight_search_cache_key

    key_economy = flight_search_cache_key("LOS", "LHR", "2026-11-01", "2026-11-10", 1, "economy")
    key_business = flight_search_cache_key("LOS", "LHR", "2026-11-01", "2026-11-10", 1, "business")
    assert key_economy != key_business


def test_activity_search_key_rounds_coordinates():
    from app.services.cache_service import activity_search_cache_key

    key1 = activity_search_cache_key(48.8566, 2.3522, 5)
    key2 = activity_search_cache_key(48.85661, 2.35221, 5)
    assert key1 == key2
