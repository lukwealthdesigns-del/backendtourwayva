"""
The Companion turn as a LangGraph workflow (Master Prompt §35-40, §83).

    classify_intent ─► check_access ─┬─► load_context ─► agent ─► END
                                     └─► deny ─────────────────► END

  classify_intent  rules first (free), then a one-word fast-model classification
  check_access     the intent's feature flag (HOTELS, FLIGHTS, PLANNER, ...) must be
                   in the user's plan; otherwise a polite refusal, no tools, no data
  load_context     only the context this intent can use (trip, preferences, memory)
  agent            a bounded tool loop that may only use THIS intent's tools; a tool the
                   model asks for outside the allow-list is refused, and every tool
                   still re-authorizes the user itself (§40)

Persistence, analytics and post-turn work (summary, memory extraction) stay in
CompanionService; this graph only decides and answers, so it is testable with
fake ports and no database.
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from typing import Any, Awaitable, Callable, Optional, TypedDict

from app.core.constants import FeatureFlag
from app.modules.companion.intents import (
    CLASSIFIER_PROMPT,
    INTENT_FEATURE,
    INTENT_TOOLS,
    TOOL_FEATURES,
    Intent,
    classify_by_rules,
    parse_intent,
)
from app.modules.planning.workflow import WorkflowSpec

logger = logging.getLogger(__name__)

MAX_TOOL_ITERATIONS = 3
_CLASSIFY_MAX_INPUT_CHARS = 1000

_FEATURE_LABELS = {
    FeatureFlag.HOTELS: "Hotel search",
    FeatureFlag.FLIGHTS: "Flight search",
    FeatureFlag.ACTIVITIES: "Activity search",
    FeatureFlag.WEATHER: "Weather forecasts",
    FeatureFlag.PLANNER: "Itinerary planning and editing",
    FeatureFlag.DISCOVER: "Destination discovery",
}


@dataclass
class CompanionPorts:
    # (messages, *, temperature, max_tokens, tools, tier) -> object with content, tool_calls,
    # model_used, prompt_tokens, completion_tokens, used_fallback
    llm: Callable[..., Awaitable[Any]]
    execute_tool: Callable[[str, dict[str, Any]], Awaitable[dict[str, Any]]]
    has_feature: Callable[[FeatureFlag], Awaitable[bool]]
    build_system_prompt: Callable[[Intent], Awaitable[str]]
    tool_schemas: list[dict[str, Any]]


class CompanionState(TypedDict, total=False):
    user_message: str
    history: list[dict[str, Any]]          # prior chat turns INCLUDING the new user message (last)
    intent: Intent
    intent_source: str                     # rules | model | fallback
    allowed: bool
    system_prompt: str
    reply: str
    model_used: Optional[str]
    usage: list[dict[str, Any]]
    tools_used: list[str]


def _usage_entry(response: Any, purpose: str) -> dict[str, Any]:
    return {
        "purpose": purpose,
        "model_used": getattr(response, "model_used", None),
        "prompt_tokens": getattr(response, "prompt_tokens", 0),
        "completion_tokens": getattr(response, "completion_tokens", 0),
        "used_fallback": getattr(response, "used_fallback", False),
    }


def make_nodes(ports: CompanionPorts) -> dict[str, Callable[[dict[str, Any]], Awaitable[dict[str, Any]]]]:
    async def classify_intent(state: CompanionState) -> dict[str, Any]:
        message = state["user_message"]
        by_rules = classify_by_rules(message)
        if by_rules is not None:
            return {"intent": by_rules, "intent_source": "rules"}
        try:
            response = await ports.llm(
                [
                    {"role": "system", "content": CLASSIFIER_PROMPT},
                    {"role": "user", "content": message[:_CLASSIFY_MAX_INPUT_CHARS]},
                ],
                temperature=0.0, max_tokens=12, tools=None, tier="fast",
            )
        except Exception as exc:  # noqa: BLE001 — classification is best-effort; the agent surfaces real outages
            logger.warning("companion_intent_classification_failed: %s", exc)
            return {"intent": Intent.UNKNOWN, "intent_source": "fallback"}
        usage = list(state.get("usage", [])) + [_usage_entry(response, "intent")]
        return {"intent": parse_intent(response.content), "intent_source": "model", "usage": usage}

    async def check_access(state: CompanionState) -> dict[str, Any]:
        feature = INTENT_FEATURE.get(state["intent"])
        if feature is None or await ports.has_feature(feature):
            return {"allowed": True}
        label = _FEATURE_LABELS.get(feature, "This feature")
        return {
            "allowed": False,
            "reply": f"{label} isn't included in your current plan, so I can't help with that request. "
                     "You can upgrade your plan to unlock it — I'm happy to help with anything else in the meantime.",
            "model_used": None,
        }

    async def load_context(state: CompanionState) -> dict[str, Any]:
        return {"system_prompt": await ports.build_system_prompt(state["intent"])}

    async def agent(state: CompanionState) -> dict[str, Any]:
        intent = state["intent"]
        # Only offer tools whose feature the user actually has (each feature checked once).
        feature_ok: dict[FeatureFlag, bool] = {}
        allowed_names: set[str] = set()
        for name in INTENT_TOOLS[intent]:
            feature = TOOL_FEATURES.get(name)
            if feature is not None and feature not in feature_ok:
                feature_ok[feature] = await ports.has_feature(feature)
            if feature is None or feature_ok[feature]:
                allowed_names.add(name)
        schemas = [s for s in ports.tool_schemas if s["function"]["name"] in allowed_names]
        messages: list[dict[str, Any]] = [{"role": "system", "content": state["system_prompt"]}] + list(state["history"])
        usage = list(state.get("usage", []))
        used: list[str] = []

        for _ in range(MAX_TOOL_ITERATIONS):
            response = await ports.llm(messages, temperature=0.7, max_tokens=800, tools=schemas, tier="default")
            usage.append(_usage_entry(response, "reply"))
            if not response.tool_calls:
                return {"reply": response.content, "model_used": response.model_used, "usage": usage, "tools_used": used}

            messages.append({
                "role": "assistant",
                "content": response.content or None,
                "tool_calls": [
                    {"id": c.id, "type": "function", "function": {"name": c.name, "arguments": json.dumps(c.arguments)}}
                    for c in response.tool_calls
                ],
            })
            for call in response.tool_calls:
                if call.name not in allowed_names:
                    result: dict[str, Any] = {"error": f"The tool '{call.name}' is not available for this request."}
                else:
                    used.append(call.name)
                    result = await ports.execute_tool(call.name, call.arguments)
                messages.append({"role": "tool", "tool_call_id": call.id, "content": json.dumps(result, default=str)})

        # Out of iterations: force a final answer with no tools so the loop cannot run forever.
        final = await ports.llm(messages, temperature=0.7, max_tokens=800, tools=None, tier="default")
        usage.append(_usage_entry(final, "reply"))
        return {"reply": final.content, "model_used": final.model_used, "usage": usage, "tools_used": used}

    async def deny(state: CompanionState) -> dict[str, Any]:
        return {}

    return {
        "classify_intent": classify_intent, "check_access": check_access, "load_context": load_context,
        "agent": agent, "deny": deny,
    }


def route_after_access(state: dict[str, Any]) -> str:
    return "load_context" if state.get("allowed") else "deny"


def build_companion_spec(ports: CompanionPorts) -> WorkflowSpec:
    spec = WorkflowSpec(
        entry="classify_intent",
        nodes=make_nodes(ports),
        edges=[("classify_intent", "check_access"), ("load_context", "agent")],
        conditionals={"check_access": (route_after_access, {"load_context": "load_context", "deny": "deny"})},
        finish=["agent", "deny"],
    )
    spec.validate()
    return spec
