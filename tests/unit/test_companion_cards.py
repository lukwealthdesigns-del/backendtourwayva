"""Structured extras on Companion replies: trips and photos collected by tools, bounded and de-duplicated, and only
real records stored as the message's `meta`."""
from __future__ import annotations

import asyncio
import uuid
from types import SimpleNamespace

from app.modules.companion import tools
from app.modules.companion.intents import INTENT_TOOLS, Intent
from app.modules.companion.service import CompanionService


def _ctx():
    return tools.ToolExecutionContext(db=None, user=SimpleNamespace(id=uuid.uuid4()), conversation_id=uuid.uuid4())


def test_note_trip_dedupes_and_caps():
    ctx = _ctx()
    ids = [uuid.uuid4() for _ in range(12)]
    for i in ids + ids[:3]:
        ctx.note_trip(i)
    assert len(ctx.collected["trip_ids"]) == 8 and len(set(ctx.collected["trip_ids"])) == 8
    assert ctx.collected["trip_ids"][0] == str(ids[0])


def test_each_context_gets_its_own_collection():
    a, b = _ctx(), _ctx()
    a.note_trip(uuid.uuid4())
    assert b.collected["trip_ids"] == []


def test_meta_is_none_when_nothing_was_collected_and_only_known_keys_are_kept():
    assert CompanionService._meta_from(None) is None
    assert CompanionService._meta_from({"trip_ids": [], "images": []}) is None
    meta = CompanionService._meta_from({"trip_ids": ["t1"], "images": [], "evil": "<script>"})
    assert meta == {"trip_ids": ["t1"]}


def test_photo_tool_is_read_only_and_limited_to_the_intents_that_need_it():
    schema = next(s for s in tools.TOOL_SCHEMAS if s["function"]["name"] == "show_place_photos")
    assert schema["function"]["parameters"]["required"] == ["queries"]
    allowed = {i for i, names in INTENT_TOOLS.items() if "show_place_photos" in names}
    assert Intent.ITINERARY_EDIT not in allowed and Intent.ACCOUNT_QUERY not in allowed and Intent.CURRENCY_QUERY not in allowed
    assert {Intent.GENERAL_TRAVEL, Intent.DISCOVERY, Intent.TRIP_QUERY} <= allowed


def test_photo_tool_attaches_distinct_photos_up_to_the_limit_and_survives_failures(monkeypatch):
    import app.modules.images.service as images

    def photo(url):
        return SimpleNamespace(url=url, thumbnail_url=url + "?t", photographer_name="P", photographer_profile_url="u", attribution_required=True, provider="unsplash")

    class FakeImages:
        async def get_or_search(self, entity, locale="en"):
            if entity == "Broken":
                raise RuntimeError("provider down")
            return photo("https://img/" + ("same" if entity.startswith("Dup") else entity))

    monkeypatch.setattr(images, "ImageService", FakeImages)
    ctx = _ctx()
    out = asyncio.run(tools._show_place_photos({"queries": ["Eiffel Tower", "Dup 1", "Dup 2", "Broken", "Louvre", "Seine"]}, ctx))
    urls = [i["url"] for i in ctx.collected["images"]]
    assert len(urls) == len(set(urls)) <= 4
    assert "Broken" not in out["attached"] and "error" not in out
    assert asyncio.run(tools._show_place_photos({"queries": []}, _ctx()))["error"]
