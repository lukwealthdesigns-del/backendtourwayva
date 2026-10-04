import pytest

from app.core.exceptions import AppError
from app.modules.companion.tools import TOOL_SCHEMAS, _parse_date, _parse_uuid


def test_parse_uuid_valid():
    value = "123e4567-e89b-12d3-a456-426614174000"
    assert str(_parse_uuid(value, "trip_id")) == value


def test_parse_uuid_invalid_raises_app_error():
    with pytest.raises(AppError):
        _parse_uuid("not-a-uuid", "trip_id")


def test_parse_uuid_none_raises_app_error():
    with pytest.raises(AppError):
        _parse_uuid(None, "trip_id")


def test_parse_date_valid():
    assert _parse_date("2026-10-01", "check_in").isoformat() == "2026-10-01"


def test_parse_date_none_returns_none():
    assert _parse_date(None, "return_date") is None


def test_parse_date_invalid_raises_app_error():
    with pytest.raises(AppError):
        _parse_date("not-a-date", "check_in")


def test_all_tool_schemas_are_valid_openai_function_shape():
    names = set()
    for schema in TOOL_SCHEMAS:
        assert schema["type"] == "function"
        fn = schema["function"]
        assert "name" in fn and "description" in fn and "parameters" in fn
        assert fn["parameters"]["type"] == "object"
        names.add(fn["name"])
    # No duplicate tool names.
    assert len(names) == len(TOOL_SCHEMAS)


def test_no_mutating_tools_exposed():
    """The model may never edit a trip directly: itinerary changes are only PROPOSED
    (propose_*) and applied after a human confirms. The single allow-listed
    exception is `create_trip_version`, an append-only checkpoint of the CURRENT
    itinerary — it changes no itinerary content and requires editor access."""
    forbidden_prefixes = ("modify_", "create_", "delete_", "update_", "cancel_")
    allowed_write_tools = {"create_trip_version"}
    for schema in TOOL_SCHEMAS:
        name = schema["function"]["name"]
        if name in allowed_write_tools:
            continue
        assert not name.startswith(forbidden_prefixes), f"{name} looks like a mutating tool"


def test_propose_tools_are_present():
    names = {s["function"]["name"] for s in TOOL_SCHEMAS}
    assert {"propose_add_trip_item", "propose_update_trip_item", "propose_delete_trip_item"} <= names


def test_deserialize_item_fields_converts_times_and_enum():
    from app.modules.companion.change_service import PendingChangeService

    payload = {
        "item_type": "activity",
        "title": "Louvre Tour",
        "start_time": "10:00:00",
        "end_time": "12:00:00",
    }
    result = PendingChangeService._deserialize_item_fields(payload)

    import datetime

    from app.core.constants import TripItemType

    assert result["item_type"] == TripItemType.ACTIVITY
    assert result["start_time"] == datetime.time(10, 0, 0)
    assert result["end_time"] == datetime.time(12, 0, 0)
    assert result["title"] == "Louvre Tour"


def test_deserialize_item_fields_handles_missing_optional_fields():
    from app.modules.companion.change_service import PendingChangeService

    result = PendingChangeService._deserialize_item_fields({"title": "New title"})
    assert result == {"title": "New title"}
