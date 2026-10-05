"""
CompanionService — Master Blueprint §§35-40, §83.

A turn runs as a LangGraph workflow (see graph.py):

    classify_intent → check_access → load_context → agent        (or → deny)

  * INTENT ROUTING: rules first, a one-word fast-model classification when
    ambiguous (intents.py); the intent decides which tools are offered, which
    feature flag is required, and which context is loaded.
  * CONTEXT (§36): the conversation summary, the current trip (membership
    re-verified), stored preferences and memories — loaded only for the
    intents that can use them.
  * TOOLS (§39-40): a bounded loop limited to the intent's allow-list; every
    tool re-authorizes the user itself. Trip changes are only ever PROPOSED
    (single items, or a whole-itinerary revision) and applied after a human
    confirms.
  * Persistence, AI-usage metering, summarization and memory extraction stay
    here, outside the graph, so a failure in any of them never fails the turn.

The LLM provider is injected (CompanionService(db, llm=...)) — there is no
module-level client to monkeypatch.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.constants import FeatureFlag, MessageRole
from app.core.exceptions import ForbiddenError, NotFoundError
from app.core.logging import get_logger
from app.db.models.conversation import Conversation, Message
from app.db.models.user import User
from app.modules.companion.context import (
    assemble_system_prompt,
    format_memories,
    format_preferences,
    format_trip_context,
)
from app.modules.companion.graph import CompanionPorts, CompanionState, build_companion_spec
from app.modules.companion.intents import NEEDS_TRIP_CONTEXT, USES_PERSONALIZATION, Intent
from app.modules.companion.memory_extraction import maybe_extract_memory
from app.modules.companion.schemas import ConversationCreateRequest
from app.modules.companion.summarization import maybe_summarize
from app.modules.companion.tools import TOOL_SCHEMAS, ToolExecutionContext, execute_tool
from app.modules.memory.service import MemoryService
from app.modules.planning.service import Runner
from app.modules.planning.workflow import WorkflowSpec, run_workflow
from app.modules.trips.service import TripService
from app.providers.llm.interface import LLMProvider
from app.providers.llm.openai_provider import OpenAIProvider
from app.repositories.conversation_repository import ConversationRepository
from app.repositories.preferences_repository import PreferencesRepository
from app.repositories.trip_repository import TripRepository

logger = get_logger(__name__)

_MAX_HISTORY_MESSAGES = 12


async def _langgraph_runner(spec: WorkflowSpec, initial: dict[str, Any]) -> dict[str, Any]:
    return await run_workflow(spec, CompanionState, initial)


def reply_timestamp(asked_at: datetime) -> datetime:
    """Timestamp for the assistant's reply: now, but always strictly after the user's message it answers, so the two can
    never tie (the column default, now(), is the transaction start and made them share one value)."""
    return max(datetime.now(timezone.utc), asked_at + timedelta(milliseconds=1))


class CompanionService:
    def __init__(self, db: AsyncSession, *, llm: Optional[LLMProvider] = None, runner: Optional[Runner] = None):
        self.db = db
        self.repo = ConversationRepository(db)
        self.memory_service = MemoryService(db)
        self.llm: LLMProvider = llm or OpenAIProvider()
        self._runner: Runner = runner or _langgraph_runner

    async def create_conversation(self, *, user_id: uuid.UUID, payload: ConversationCreateRequest) -> Conversation:
        if payload.trip_id is not None:
            # Verify the user actually has access to the trip they're
            # attaching this conversation to (never trust the client).
            await TripService(self.db).get_trip_authorized(trip_id=payload.trip_id, user_id=user_id)

        conversation = Conversation(user_id=user_id, trip_id=payload.trip_id, title=payload.title)
        await self.repo.create_conversation(conversation)
        await self.db.commit()
        return conversation

    async def list_conversations(self, user_id: uuid.UUID):
        return await self.repo.list_conversations_for_user(user_id)

    async def get_owned_conversation(self, *, conversation_id: uuid.UUID, user_id: uuid.UUID) -> Conversation:
        conversation = await self.repo.get_conversation(conversation_id)
        if conversation is None:
            raise NotFoundError("Conversation not found.")
        if conversation.user_id != user_id:
            raise ForbiddenError("You do not have access to this conversation.")
        return conversation

    async def list_messages(self, conversation_id: uuid.UUID, *, limit: int = 50):
        return await self.repo.list_messages(conversation_id, limit=limit)

    async def send_message(
        self, *, conversation: Conversation, user: User, content: str, defer_post_turn: bool = False
    ) -> Message:
        """Runs one turn and returns the saved reply. With `defer_post_turn=True` the (LLM-backed) summarization and
        memory extraction are NOT run here: the caller runs `post_turn()` after responding, so the user is not kept
        waiting for two extra model calls that do not change the reply."""
        asked_at = datetime.now(timezone.utc)
        await self.repo.add_message(
            Message(conversation_id=conversation.id, role=MessageRole.USER, content=content, created_at=asked_at)
        )

        history = await self.repo.list_messages(conversation.id, limit=_MAX_HISTORY_MESSAGES)
        chat_history = [
            {"role": "assistant" if m.role == MessageRole.ASSISTANT else "user", "content": m.content} for m in history
        ]

        ports = self._build_ports(conversation, user)
        state = await self._runner(
            build_companion_spec(ports),
            {"user_message": content, "history": chat_history, "usage": [], "tools_used": []},
        )

        reply = state.get("reply") or "Sorry, I could not produce an answer. Please try again."
        # An explicit timestamp, strictly after the user's message: the column default (now()) is the transaction start
        # and would make both messages share one timestamp, so they could be listed in either order.
        answered_at = reply_timestamp(asked_at)
        assistant_message = Message(
            conversation_id=conversation.id, role=MessageRole.ASSISTANT, content=reply,
            model_used=state.get("model_used"), created_at=answered_at, meta=self._meta_from(getattr(self, "_collected", None)),
        )
        await self.repo.add_message(assistant_message)
        await self.db.commit()

        await self._record_turn(user, state, trip_id=conversation.trip_id)

        if not defer_post_turn:
            await self.post_turn(conversation=conversation, user=user, content=content, reply=reply)

        return assistant_message

    @staticmethod
    def _meta_from(collected: Optional[dict]) -> Optional[dict]:
        """What the tools gathered for this reply, or None when there is nothing to attach (keeps old rows and plain
        answers NULL). Only trip ids and photo records are ever stored; no free text from the model."""
        if not collected:
            return None
        meta = {k: collected[k] for k in ("trip_ids", "images") if collected.get(k)}
        return meta or None

    # ------------------------------------------------------------------
    # Ports for the graph
    # ------------------------------------------------------------------
    def _build_ports(self, conversation: Conversation, user: User) -> CompanionPorts:
        ctx = ToolExecutionContext(db=self.db, user=user, conversation_id=conversation.id, llm=self.llm)
        self._collected = ctx.collected   # read by send_message after the graph finishes

        async def llm(messages, *, temperature, max_tokens, tools, tier):
            return await self.llm.generate(
                messages, temperature=temperature, max_tokens=max_tokens, tools=tools, tier=tier
            )

        async def run_tool(name: str, arguments: dict[str, Any]) -> dict[str, Any]:
            logger.info("companion_tool_call", tool=name, user_id=str(user.id))
            return await execute_tool(name, arguments, ctx)

        async def has_feature(flag: FeatureFlag) -> bool:
            return await self._has_feature(user, flag)

        async def build_system_prompt(intent: Intent) -> str:
            trip_context = ""
            if intent in NEEDS_TRIP_CONTEXT and conversation.trip_id:
                trip_context = await self._trip_context(conversation.trip_id, user)
            preferences = memories = ""
            if intent in USES_PERSONALIZATION:
                preferences = format_preferences(await PreferencesRepository(self.db).get(user.id))
                if await has_feature(FeatureFlag.MEMORY):        # remembering is a plan feature
                    memories = format_memories(
                        [m.content for m in await self.memory_service.list_enabled_for_user(user.id)]
                    )
            return assemble_system_prompt(
                intent, summary=conversation.summary, trip_context=trip_context,
                preferences=preferences, memories=memories, language=user.language,
            )

        return CompanionPorts(
            llm=llm, execute_tool=run_tool, has_feature=has_feature,
            build_system_prompt=build_system_prompt, tool_schemas=TOOL_SCHEMAS,
        )

    async def _has_feature(self, user: User, flag: FeatureFlag) -> bool:
        from app.modules.entitlements.service import EntitlementService

        return await EntitlementService(self.db).has_feature(user.id, flag)

    async def _trip_context(self, trip_id: uuid.UUID, user: User) -> str:
        """The conversation's trip as compact text. Membership is re-verified
        NOW (the user may have been removed since the conversation started);
        on any failure the model simply gets no trip context."""
        try:
            trip = await TripService(self.db).get_trip_authorized(trip_id=trip_id, user_id=user.id)
            repo = TripRepository(self.db)
            days = await repo.list_days_for_trip(trip_id)
            return format_trip_context(trip, [(d, list(await repo.list_items_for_day(d.id))) for d in days])
        except Exception as exc:  # noqa: BLE001
            logger.warning("companion_trip_context_unavailable", error=str(exc))
            return ""

    async def post_turn(self, *, conversation: Conversation, user: User, content: str, reply: str) -> None:
        """Work that follows a saved reply: rolling summary and memory extraction. Best effort, never raises."""
        try:
            all_messages = await self.repo.list_all_messages(conversation.id)
            updated_summary = await maybe_summarize(conversation, list(all_messages), llm=self.llm)
            if updated_summary:
                conversation.summary = updated_summary
                await self.repo.save_conversation(conversation)
                await self.db.commit()
        except Exception as exc:  # noqa: BLE001
            logger.warning("companion_summarization_failed", error=str(exc))

        try:
            extracted = None
            if await self._has_feature(user, FeatureFlag.MEMORY):     # no plan feature => nothing is remembered
                extracted = await maybe_extract_memory(user_message=content, assistant_reply=reply, llm=self.llm)
            if extracted:
                await self.memory_service.create_extracted(
                    user_id=user.id, content=extracted.content, confidence=extracted.confidence
                )
        except Exception as exc:  # noqa: BLE001
            logger.warning("companion_memory_extraction_failed", error=str(exc))


    async def _record_turn(self, user: User, state: dict[str, Any], *, trip_id: Optional[uuid.UUID] = None) -> None:
        """AI usage per model call (§57-58) and the routing decision for analytics —
        best-effort, never blocks the turn. `trip_id` is the conversation's trip
        (None for a general, trip-less conversation) so admin cost-by-trip
        reporting covers Companion turns too, not just itinerary generation."""
        try:
            from app.modules.analytics.service import AnalyticsService

            analytics = AnalyticsService(self.db)
            for entry in state.get("usage", []):
                if not entry.get("model_used"):
                    continue
                await analytics.record_ai_usage(
                    user_id=user.id, feature=f"companion_{entry['purpose']}", model_used=entry["model_used"],
                    prompt_tokens=entry["prompt_tokens"], completion_tokens=entry["completion_tokens"],
                    used_fallback=entry["used_fallback"], trip_id=trip_id,
                )
            intent = state.get("intent")
            await analytics.record_event(
                user_id=user.id, event_type="companion_turn",
                properties={
                    "intent": intent.value if intent else None, "intent_source": state.get("intent_source"),
                    "allowed": state.get("allowed", True), "tools_used": state.get("tools_used", []),
                },
            )
        except Exception as exc:  # noqa: BLE001
            await self.db.rollback()
            logger.warning("companion_usage_recording_failed", error=str(exc))
