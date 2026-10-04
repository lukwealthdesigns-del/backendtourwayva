"""
Conversation summarization (Master Blueprint §38): once a
conversation's message history grows past a threshold, older messages
are condensed into a single rolling summary stored on
`Conversation.summary`, so the LLM context sent on every turn doesn't
grow unbounded. The most recent `KEEP_VERBATIM_COUNT` messages are
always still sent in full; only messages *older* than that window are
ever folded into the summary.

Deliberately simple: one "summarize this" LLM call per threshold
crossing, extending the previous summary if one exists — no sliding
overlap, no structured extraction beyond a short prose paragraph.
"""
from __future__ import annotations

from typing import Optional

from app.core.constants import MessageRole
from app.db.models.conversation import Conversation, Message
from app.providers.llm.interface import LLMProvider
from app.providers.llm.openai_provider import OpenAIProvider

_llm = OpenAIProvider()

SUMMARIZE_AFTER_MESSAGE_COUNT = 24
KEEP_VERBATIM_COUNT = 12  # matches CompanionService._MAX_HISTORY_MESSAGES

_SUMMARY_PROMPT = (
    "Summarize the following travel-assistant conversation history into a short "
    "paragraph capturing durable facts, preferences, and decisions the assistant "
    "should remember going forward. Be concise — a few sentences, not a transcript."
)


async def maybe_summarize(
    conversation: Conversation, all_messages: list[Message], llm: Optional[LLMProvider] = None
) -> Optional[str]:
    """Returns an updated summary string if summarization ran, else None
    (below threshold — caller should leave conversation.summary as-is)."""
    if len(all_messages) < SUMMARIZE_AFTER_MESSAGE_COUNT:
        return None

    to_summarize = all_messages[:-KEEP_VERBATIM_COUNT]
    if not to_summarize:
        return None

    transcript = "\n".join(
        f"{'User' if m.role == MessageRole.USER else 'Assistant'}: {m.content}" for m in to_summarize
    )

    prompt = _SUMMARY_PROMPT
    if conversation.summary:
        prompt += f"\n\nExisting summary to update/extend:\n{conversation.summary}"

    response = await (llm or _llm).generate(
        [
            {"role": "system", "content": prompt},
            {"role": "user", "content": transcript},
        ],
        max_tokens=300,
        tier="fast",
    )
    return response.content
