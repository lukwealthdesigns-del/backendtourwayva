"""The Companion workflow with fake ports: routing, feature gating, tool allow-lists."""
from __future__ import annotations

import asyncio
from dataclasses import dataclass, field

import pytest

from app.core.constants import FeatureFlag
from app.modules.companion.graph import CompanionPorts, CompanionState, build_companion_spec
from app.modules.companion.intents import INTENT_TOOLS, Intent
from app.modules.planning.workflow import run_locally
from app.providers.llm.interface import ToolCall


@dataclass
class Reply:
    content: str = ""
    tool_calls: list = field(default_factory=list)
    model_used: str = "test-model"
    prompt_tokens: int = 10
    completion_tokens: int = 5
    used_fallback: bool = False


def schema(name):
    return {"type": "function", "function": {"name": name, "description": name, "parameters": {"type": "object"}}}


ALL_TOOLS = sorted({t for tools in INTENT_TOOLS.values() for t in tools})


class Harness:
    def __init__(self, replies, *, features=None, tool_results=None):
        self.replies = list(replies)
        self.llm_calls = []
        self.executed = []
        self.prompted = []
        self.denied_features = set(features or [])
        self.tool_results = tool_results or {}

    def ports(self):
        async def llm(messages, *, temperature, max_tokens, tools, tier):
            self.llm_calls.append({"messages": messages, "tools": tools, "tier": tier, "temperature": temperature})
            reply = self.replies.pop(0) if len(self.replies) > 1 else self.replies[0]
            if isinstance(reply, Exception):
                raise reply
            return reply

        async def execute_tool(name, args):
            self.executed.append((name, args))
            return self.tool_results.get(name, {"ok": True})

        async def has_feature(flag):
            return flag not in self.denied_features

        async def build_system_prompt(intent):
            self.prompted.append(intent)
            return f"SYSTEM for {intent.value}"

        return CompanionPorts(llm=llm, execute_tool=execute_tool, has_feature=has_feature,
                              build_system_prompt=build_system_prompt, tool_schemas=[schema(n) for n in ALL_TOOLS])

    def run(self, message):
        state = {"user_message": message, "history": [{"role": "user", "content": message}], "usage": []}
        return asyncio.run(run_locally(build_companion_spec(self.ports()), state))


def offered(call):
    return {t["function"]["name"] for t in (call["tools"] or [])}


def test_obvious_intents_need_no_classification_call_and_use_only_their_tools():
    h = Harness([Reply("It will be sunny.")])
    state = h.run("What's the weather like in Paris?")
    assert state["intent"] == Intent.WEATHER_QUERY and state["intent_source"] == "rules"
    assert len(h.llm_calls) == 1                                    # only the answer: no classifier call
    assert offered(h.llm_calls[0]) == set(INTENT_TOOLS[Intent.WEATHER_QUERY])
    assert h.llm_calls[0]["messages"][0]["content"] == "SYSTEM for WEATHER_QUERY"
    assert state["reply"] == "It will be sunny."


def test_ambiguous_messages_are_classified_by_the_fast_model_at_temperature_zero():
    h = Harness([Reply("HOTEL_SEARCH"), Reply("Here are options.")])
    state = h.run("somewhere comfy to sleep near the beach, please")
    assert state["intent"] == Intent.HOTEL_SEARCH and state["intent_source"] == "model"
    classifier, answer = h.llm_calls
    assert classifier["tier"] == "fast" and classifier["temperature"] == 0.0 and classifier["tools"] is None
    assert offered(answer) == set(INTENT_TOOLS[Intent.HOTEL_SEARCH])
    assert [u["purpose"] for u in state["usage"]] == ["intent", "reply"]


def test_a_failed_classification_degrades_to_the_read_only_general_tool_set():
    h = Harness([RuntimeError("classifier down"), Reply("Sure.")])
    state = h.run("hello there")
    assert state["intent"] == Intent.UNKNOWN and state["intent_source"] == "fallback"
    tools = offered(h.llm_calls[-1])
    assert tools and not any(t.startswith("propose_") for t in tools)      # never write tools when unsure


def test_feature_not_in_plan_short_circuits_with_no_tools_no_context_no_answer_call():
    h = Harness([Reply("should never be called")], features=[FeatureFlag.HOTELS])
    state = h.run("Find me a hotel in Lisbon")
    assert state["intent"] == Intent.HOTEL_SEARCH and state["allowed"] is False
    assert "Hotel search" in state["reply"] and "plan" in state["reply"]
    assert h.llm_calls == [] and h.prompted == [] and h.executed == []


def test_denying_one_feature_does_not_block_unrelated_intents():
    h = Harness([Reply("EUR 0.92")], features=[FeatureFlag.HOTELS, FeatureFlag.FLIGHTS])
    state = h.run("Convert 100 USD to EUR")
    assert state["allowed"] is True and state["reply"] == "EUR 0.92"


def test_allowed_tool_calls_are_executed_and_fed_back_to_the_model():
    call = ToolCall(id="c1", name="get_weather", arguments={"latitude": 48.8, "longitude": 2.3})
    h = Harness([Reply(tool_calls=[call]), Reply("18°C and sunny.")], tool_results={"get_weather": {"temp": 18}})
    state = h.run("What's the weather in Paris?")
    assert h.executed == [("get_weather", {"latitude": 48.8, "longitude": 2.3})]
    followup = h.llm_calls[1]["messages"]
    assert followup[-1]["role"] == "tool" and '"temp": 18' in followup[-1]["content"]
    assert followup[-2]["tool_calls"][0]["function"]["name"] == "get_weather"
    assert state["reply"] == "18°C and sunny." and state["tools_used"] == ["get_weather"]


def test_a_tool_outside_the_intents_allow_list_is_refused_and_never_executed():
    evil = ToolCall(id="c1", name="propose_delete_trip_item", arguments={"item_id": "x"})
    h = Harness([Reply(tool_calls=[evil]), Reply("I can't do that here.")])
    state = h.run("What's the weather in Paris?")                   # WEATHER_QUERY: no write tools
    assert h.executed == []
    assert "not available for this request" in h.llm_calls[1]["messages"][-1]["content"]
    assert state["tools_used"] == []


def test_itinerary_edit_is_the_only_intent_offered_the_proposal_tools():
    h = Harness([Reply("Proposed.")])
    h.run("Remove Day 3")
    assert {"propose_itinerary_revision", "propose_delete_trip_item"} <= offered(h.llm_calls[0])
    h2 = Harness([Reply("ok")])
    h2.run("What is planned for day 2?")
    assert not any(t.startswith("propose_") for t in offered(h2.llm_calls[0]))


def test_tool_loop_is_bounded_then_forces_a_final_answer_without_tools():
    call = ToolCall(id="c", name="get_weather", arguments={})
    h = Harness([Reply(tool_calls=[call])] * 3 + [Reply("Giving up and answering.")])
    state = h.run("What's the weather in Paris?")
    assert len(h.llm_calls) == 4 and h.llm_calls[-1]["tools"] is None
    assert state["reply"] == "Giving up and answering." and len(h.executed) == 3


def test_provider_outage_in_the_answer_step_propagates_to_the_caller():
    from app.core.exceptions import ProviderUnavailableError

    h = Harness([ProviderUnavailableError("down")])
    with pytest.raises(ProviderUnavailableError):
        h.run("What's the weather in Paris?")


def test_langgraph_runs_the_companion_spec():
    pytest.importorskip("langgraph")
    from app.modules.planning.workflow import run_workflow

    h = Harness([Reply("Sunny.")])
    state = {"user_message": "What's the weather in Paris?", "history": [{"role": "user", "content": "x"}], "usage": []}
    result = asyncio.run(run_workflow(build_companion_spec(h.ports()), CompanionState, state))
    assert result["reply"] == "Sunny." and result["intent"] == Intent.WEATHER_QUERY


# ---------------------------------------------------------------------------
# Tool-level entitlement (the intent allowing a request is not enough)
# ---------------------------------------------------------------------------
def test_tools_the_user_is_not_entitled_to_are_not_offered_even_when_the_intent_allows_the_request():
    h = Harness([Reply("Day 2 is museums.")], features=[FeatureFlag.WEATHER, FeatureFlag.MEMORY])
    state = h.run("What's planned for day 2?")                       # TRIP_QUERY needs no feature itself
    assert state["intent"] == Intent.TRIP_QUERY and state["allowed"] is True
    tools = offered(h.llm_calls[0])
    assert "get_trip" in tools and "get_trip_itinerary" in tools
    assert "get_weather" not in tools and "get_my_memories" not in tools        # their plan features are missing


def test_a_model_asking_for_a_tool_it_was_not_offered_is_refused_and_nothing_runs():
    call = ToolCall(id="c1", name="get_weather", arguments={"latitude": 1.0, "longitude": 2.0})
    h = Harness([Reply(tool_calls=[call]), Reply("I can't check the weather on your plan.")], features=[FeatureFlag.WEATHER])
    state = h.run("What's planned for day 2?")
    assert h.executed == [] and state["tools_used"] == []
    assert "not available for this request" in h.llm_calls[1]["messages"][-1]["content"]


def test_each_feature_is_checked_once_per_turn_not_once_per_tool():
    checks = []
    h = Harness([Reply("ok")])
    original = h.ports

    def counting_ports():
        ports = original()
        real = ports.has_feature

        async def has_feature(flag):
            checks.append(flag)
            return await real(flag)

        ports.has_feature = has_feature
        return ports

    h.ports = counting_ports
    h.run("What's the weather like in Paris?")                       # WEATHER intent: 1 intent check + tool features
    assert len(checks) == len(set(checks)) + 1                       # only the intent gate repeats a feature (WEATHER)
