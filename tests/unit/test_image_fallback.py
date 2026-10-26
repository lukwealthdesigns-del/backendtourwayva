"""Image fallback: an honest, labelled stand-in when the exact place has no photo (app/modules/images)."""
from __future__ import annotations

import asyncio
from types import SimpleNamespace as NS

from app.core.exceptions import NotFoundError
from app.modules.images import service as svc
from app.modules.images.query import category_hint, image_fallback_candidates


def _run(coro):
    return asyncio.run(coro)


def test_a_restaurant_falls_back_to_that_kind_of_place_then_the_area_and_never_to_its_own_name():
    out = image_fallback_candidates("Lukmaan Restaurant, Stone Town, Zanzibar, Zanzibar, Tanzania")
    assert out == [("restaurant food Stone Town", "category"), ("Stone Town Zanzibar", "area"), ("Zanzibar Tanzania", "area")]
    assert all("Lukmaan" not in q for q, _ in out)


def test_category_words_are_whole_words_and_case_insensitive():
    assert category_hint("Park Inn by Radisson") == "hotel room"          # a hotel that happens to contain "park"
    assert category_hint("The Barber Shop") is None                    # "bar" inside "Barber" is not the word "bar"
    assert category_hint("KENDWA BEACH") == "tropical beach" and category_hint("Freddie Mercury Museum") == "museum interior"
    assert category_hint("") is None


def test_a_bare_name_with_no_area_or_type_has_no_fallback_so_nothing_is_invented():
    assert image_fallback_candidates("Zorbleflax") == [] and image_fallback_candidates("") == []


class _Provider:
    def __init__(self, hits):
        self.hits, self.queries = hits, []

    async def search(self, query):
        self.queries.append(query)
        return self.hits.get(query)


def _photo(tag):
    return NS(url=f"https://img/{tag}", thumbnail_url=f"https://img/{tag}-t", width=10, height=10, photographer_name="P",
              photographer_profile_url="https://u/p", attribution_required=True, provider="unsplash")


class Env:
    def __init__(self, monkeypatch, hits):
        self.store = {}
        env = self
        self.provider = _Provider(hits)

        class FakeCache:
            @staticmethod
            async def get_json(key):
                return env.store.get(key)

            @staticmethod
            async def set_json(key, value, ttl):
                env.store[key] = value

        monkeypatch.setattr(svc, "_provider", self.provider)
        monkeypatch.setattr(svc, "CacheService", FakeCache)


ENTITY = "Lukmaan Restaurant, Stone Town, Zanzibar, Zanzibar, Tanzania"


def test_an_exact_photo_is_marked_exact_and_no_fallback_is_searched(monkeypatch):
    env = Env(monkeypatch, {"Lukmaan Restaurant, Stone Town, Zanzibar, Tanzania": _photo("exact")})
    out = _run(svc.ImageService().get_or_search(ENTITY))
    assert out.match == "exact" and out.url == "https://img/exact" and len(env.provider.queries) == 1


def test_when_the_exact_name_has_no_photo_a_labelled_stand_in_is_returned_not_an_error(monkeypatch):
    env = Env(monkeypatch, {"Stone Town Zanzibar": _photo("area")})
    out = _run(svc.ImageService().get_or_search(ENTITY))
    assert out.match == "area" and out.url == "https://img/area"
    assert "restaurant food Stone Town" in env.provider.queries           # the closer match was tried first


def test_a_category_photo_wins_over_an_area_photo_when_both_exist(monkeypatch):
    Env(monkeypatch, {"restaurant food Stone Town": _photo("food"), "Stone Town Zanzibar": _photo("area")})
    out = _run(svc.ImageService().get_or_search(ENTITY))
    assert out.match == "category" and out.url == "https://img/food"


def test_the_same_neighbourhood_is_looked_up_once_for_many_venues(monkeypatch):
    env = Env(monkeypatch, {"Stone Town Zanzibar": _photo("area")})
    for name in ("Lukmaan Restaurant", "Passing Show Restaurant", "The Silk Route", "Stone Town Cafe"):
        _run(svc.ImageService().get_or_search(f"{name}, Stone Town, Zanzibar, Zanzibar, Tanzania"))
    assert env.provider.queries.count("Stone Town Zanzibar") == 1        # later venues reuse the cached neighbourhood photo


def test_a_second_request_is_served_from_cache_without_the_provider(monkeypatch):
    env = Env(monkeypatch, {"Stone Town Zanzibar": _photo("area")})
    _run(svc.ImageService().get_or_search(ENTITY))
    calls = len(env.provider.queries)
    again = _run(svc.ImageService().get_or_search(ENTITY))
    assert again.source == "cache" and again.match == "area" and len(env.provider.queries) == calls


def test_with_nothing_at_any_level_it_is_a_remembered_not_found(monkeypatch):
    env = Env(monkeypatch, {})
    for _ in range(2):
        try:
            _run(svc.ImageService().get_or_search(ENTITY))
        except NotFoundError:
            continue
        raise AssertionError("expected NotFoundError")
    first_round = len(env.provider.queries)
    assert first_round > 0 and env.store[svc.image_cache_key(ENTITY, "en")] == {"miss": True}
