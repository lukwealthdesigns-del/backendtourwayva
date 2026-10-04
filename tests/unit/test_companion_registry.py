"""Consistency of the Companion's tool registry with its intent policy, and LLM routing."""
import asyncio

from app.core.config import settings
from app.core.constants import PendingChangeAction
from app.modules.companion.intents import INTENT_TOOLS
from app.modules.companion.tools import _HANDLERS, TOOL_SCHEMAS
from app.providers.llm.mock_provider import MockLLMProvider
from app.providers.llm.openai_provider import OpenAIProvider


def test_every_tool_has_a_schema_a_handler_and_at_least_one_intent():
    schema_names = {t["function"]["name"] for t in TOOL_SCHEMAS}
    allow_listed = {name for tools in INTENT_TOOLS.values() for name in tools}
    assert schema_names == set(_HANDLERS) == allow_listed


def test_tool_schemas_are_valid_function_definitions():
    for tool in TOOL_SCHEMAS:
        function = tool["function"]
        assert tool["type"] == "function" and function["description"]
        params = function["parameters"]
        assert params["type"] == "object"
        assert set(params.get("required", [])) <= set(params.get("properties", {})), function["name"]


def test_write_capable_tools_are_only_reachable_from_itinerary_edit():
    writers = {"propose_add_trip_item", "propose_update_trip_item", "propose_delete_trip_item",
               "propose_itinerary_revision", "create_trip_version"}
    reachable_from = {intent.value for intent, tools in INTENT_TOOLS.items() if writers & set(tools)}
    assert reachable_from == {"ITINERARY_EDIT"}


def test_revision_is_a_real_pending_change_action():
    assert PendingChangeAction("revise_trip") is PendingChangeAction.REVISE_TRIP


def test_models_are_routed_by_tier(monkeypatch):
    monkeypatch.setattr(settings, "AI_PRIMARY_MODEL", "primary")
    monkeypatch.setattr(settings, "AI_FAST_MODEL", "cheap")
    monkeypatch.setattr(settings, "AI_STRONG_MODEL", None)
    assert OpenAIProvider.model_for_tier("fast") == "cheap"
    assert OpenAIProvider.model_for_tier("strong") == "primary"       # unset tier falls back to the primary model
    assert OpenAIProvider.model_for_tier("default") == "primary"


def test_mock_provider_records_the_requested_tier():
    mock = MockLLMProvider()
    asyncio.run(mock.generate([{"role": "user", "content": "hi"}], tier="fast"))
    assert mock.calls[0]["tier"] == "fast"
