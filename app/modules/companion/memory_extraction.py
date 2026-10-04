"""
Automatic memory extraction (Master Blueprint §37) — the piece that
was previously missing: memories no longer have to be explicitly
POSTed by the user. After each Companion turn, this makes one
lightweight, tools-free LLM call asking whether the exchange revealed
a durable travel preference worth remembering (e.g. "prefers boutique
hotels over chains"), as opposed to a one-off, transient detail (e.g.
"is hungry right now").

Deliberately conservative:
  - Runs as its own isolated call (no tools, no conversation history
    beyond this one exchange) so it can't be hijacked by anything the
    tool loop did, and can't itself trigger more tool calls.
  - Only ever proposes source=COMPANION_EXTRACTED memories with a
    confidence score attached (Blueprint §37: memory must have
    source + confidence) — never silently upgrades to user_stated.
  - Caller (CompanionService) applies a confidence threshold and a
    simple case-insensitive-substring dedupe against the user's
    existing enabled memories before actually storing anything, and
    treats any failure here as non-fatal (same pattern as
    summarization) — extraction must never break a turn whose reply
    already succeeded.
  - The person retains full control afterward via the existing
    view/edit/delete/disable endpoints (app/modules/memory) — this
    only adds a new *source* of proposed memories, it doesn't change
    how they're managed once created.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Optional

from app.core.logging import get_logger
from app.providers.llm.interface import LLMProvider
from app.providers.llm.openai_provider import OpenAIProvider

logger = get_logger(__name__)

_llm = OpenAIProvider()

MIN_CONFIDENCE_TO_STORE = 0.6

_EXTRACTION_PROMPT = (
    "You analyze one exchange between a user and a travel-assistant chatbot to decide "
    "whether it revealed a durable travel preference worth remembering for future "
    "conversations (e.g. preferred hotel category, budget style, walking tolerance, "
    "favorite activity types, dietary needs, preferred airlines, accessibility needs).\n\n"
    "Do NOT extract: one-off facts about a specific trip ('going to Paris in June'), "
    "transient states ('tired today', 'hungry'), or anything not stated or clearly implied "
    "by the user themselves.\n\n"
    "Respond with ONLY a JSON object, no other text:\n"
    '{"has_memory": true|false, "content": "<short factual statement, or empty string>", '
    '"confidence": <0.0 to 1.0>}'
)


@dataclass(frozen=True)
class ExtractedMemory:
    content: str
    confidence: float


async def maybe_extract_memory(
    *, user_message: str, assistant_reply: str, llm: Optional[LLMProvider] = None
) -> Optional[ExtractedMemory]:
    """Returns an ExtractedMemory if the exchange revealed something
    worth remembering above MIN_CONFIDENCE_TO_STORE, else None. Never
    raises — any failure (LLM unavailable, bad JSON) is logged and
    treated as "nothing to extract"."""
    try:
        response = await (llm or _llm).generate(
            [
                {"role": "system", "content": _EXTRACTION_PROMPT},
                {
                    "role": "user",
                    "content": f"User: {user_message}\n\nAssistant: {assistant_reply}",
                },
            ],
            temperature=0.0,
            max_tokens=150,
            tier="fast",
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("memory_extraction_llm_failed", error=str(exc))
        return None

    try:
        data = json.loads(response.content)
    except (json.JSONDecodeError, TypeError):
        logger.warning("memory_extraction_bad_json", raw=response.content[:200])
        return None

    if not data.get("has_memory") or not data.get("content"):
        return None

    try:
        confidence = float(data.get("confidence", 0.0))
    except (TypeError, ValueError):
        confidence = 0.0

    if confidence < MIN_CONFIDENCE_TO_STORE:
        return None

    return ExtractedMemory(content=str(data["content"]).strip()[:1000], confidence=confidence)
