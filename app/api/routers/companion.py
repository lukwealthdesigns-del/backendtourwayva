"""Companion endpoints (Master Blueprint §35) — scope caveats are
documented in app/modules/companion/service.py and __init__.py."""
from __future__ import annotations

import uuid

import asyncio
import json

from fastapi import APIRouter, BackgroundTasks, Depends, File, Response, UploadFile, status
from fastapi.responses import StreamingResponse
from starlette.background import BackgroundTask
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies import get_current_user, rate_limit, require_feature
from app.core.constants import FeatureFlag
from app.core.exceptions import AppError
from app.db.models.user import User
from app.core.logging import get_logger
from app.db.session import AsyncSessionLocal, get_db
from app.modules.companion.schemas import (
    ConfirmChangeResult,
    ConversationCreateRequest,
    ConversationResponse,
    MessageCreateRequest,
    MessageResponse,
    PendingChangeResponse,
    VoiceMessageResponse,
)
from app.modules.companion.service import CompanionService
from app.modules.companion.speech_service import SpeechService

router = APIRouter(prefix="/companion", tags=["Companion"])


@router.post("/conversations", response_model=ConversationResponse, status_code=status.HTTP_201_CREATED, dependencies=[Depends(require_feature(FeatureFlag.COMPANION))])
async def create_conversation(
    payload: ConversationCreateRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    conversation = await CompanionService(db).create_conversation(user_id=current_user.id, payload=payload)
    return ConversationResponse.model_validate(conversation)


@router.get("/conversations", response_model=list[ConversationResponse])
async def list_conversations(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    conversations = await CompanionService(db).list_conversations(current_user.id)
    return [ConversationResponse.model_validate(c) for c in conversations]


@router.get("/conversations/{conversation_id}/messages", response_model=list[MessageResponse])
async def list_messages(
    conversation_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    service = CompanionService(db)
    await service.get_owned_conversation(conversation_id=conversation_id, user_id=current_user.id)
    messages = await service.list_messages(conversation_id)
    return [MessageResponse.model_validate(m) for m in messages]


logger = get_logger(__name__)


async def _post_turn(conversation_id: uuid.UUID, user_id: uuid.UUID, content: str, reply: str) -> None:
    """Runs after the reply has been sent: rolling summary + memory extraction on a fresh session. Never raises."""
    try:
        async with AsyncSessionLocal() as session:
            service = CompanionService(session)
            conversation = await service.get_owned_conversation(conversation_id=conversation_id, user_id=user_id)
            from app.repositories.user_repository import UserRepository

            user = await UserRepository(session).get_by_id(user_id)
            if user is not None:
                await service.post_turn(conversation=conversation, user=user, content=content, reply=reply)
    except Exception as exc:  # noqa: BLE001 - the reply is already delivered
        logger.warning("companion_post_turn_failed", error=str(exc))


def _sse(event: str, data: dict) -> str:
    """One Server-Sent Events frame."""
    return f"event: {event}\ndata: {json.dumps(data, default=str)}\n\n"


_KEEPALIVE_SECONDS = 15


@router.post(
    "/conversations/{conversation_id}/messages/stream",
    dependencies=[
        Depends(require_feature(FeatureFlag.COMPANION)),
        Depends(rate_limit(bucket="companion:message", max_requests=20, window_seconds=60, per="user")),
    ],
    responses={200: {"content": {"text/event-stream": {}}, "description": "Server-Sent Events: status, delta, reset, done, error."}},
)
async def stream_message(
    conversation_id: uuid.UUID,
    payload: MessageCreateRequest,
    current_user: User = Depends(get_current_user),
):
    """Same turn as `POST .../messages`, but the reply arrives as it is written (Server-Sent Events).

    Events (`event:` name -> JSON `data:`):
      * `status`  {phase: "thinking" | "tool", tool?}  - progress while the assistant works
      * `delta`   {text}                                - a piece of the reply, in order
      * `reset`   {}                                    - discard the text shown so far (it was only a preamble to a tool call)
      * `done`    {message: MessageResponse}            - the saved reply (authoritative; replace the streamed text with it)
      * `error`   {error_code, message}                 - the turn failed; nothing was saved for the reply
    The request's own database session is closed before the body is streamed, so the turn runs on a fresh session.
    If the client disconnects the turn is cancelled."""
    holder: dict = {}
    user_id = current_user.id

    async def events():
        queue: asyncio.Queue = asyncio.Queue()

        async def emit(event: dict) -> None:
            await queue.put(event)

        async def work() -> None:
            try:
                async with AsyncSessionLocal() as session:
                    from app.repositories.user_repository import UserRepository

                    service = CompanionService(session)
                    user = await UserRepository(session).get_by_id(user_id)
                    conversation = await service.get_owned_conversation(conversation_id=conversation_id, user_id=user_id)
                    reply = await service.send_message(
                        conversation=conversation, user=user, content=payload.content, defer_post_turn=True, emit=emit
                    )
                    holder["reply"] = reply.content
                    await queue.put({"type": "done", "message": MessageResponse.model_validate(reply).model_dump(mode="json")})
            except AppError as exc:
                await queue.put({"type": "error", "error_code": exc.error_code, "message": exc.message})
            except asyncio.CancelledError:
                raise
            except Exception:  # noqa: BLE001 - never leak internals into the stream
                logger.exception("companion_stream_failed")
                await queue.put({"type": "error", "error_code": "internal_error", "message": "Something went wrong. Please try again."})
            finally:
                await queue.put(None)

        task = asyncio.create_task(work())
        yield _sse("status", {"phase": "thinking"})
        try:
            while True:
                try:
                    item = await asyncio.wait_for(queue.get(), timeout=_KEEPALIVE_SECONDS)
                except asyncio.TimeoutError:
                    yield ": keepalive\n\n"            # keeps proxies from closing a quiet connection during a slow tool
                    continue
                if item is None:
                    break
                yield _sse(item.pop("type"), item)
        finally:
            if not task.done():
                task.cancel()                          # the client went away: stop spending on this turn

    async def after_stream() -> None:
        if "reply" in holder:
            await _post_turn(conversation_id, user_id, payload.content, holder["reply"])

    return StreamingResponse(
        events(), media_type="text/event-stream", background=BackgroundTask(after_stream),
        headers={"Cache-Control": "no-cache, no-transform", "X-Accel-Buffering": "no", "Connection": "keep-alive"},
    )


@router.post(
    "/conversations/{conversation_id}/messages", response_model=MessageResponse, status_code=status.HTTP_201_CREATED,
    dependencies=[
        Depends(require_feature(FeatureFlag.COMPANION)),
        Depends(rate_limit(bucket="companion:message", max_requests=20, window_seconds=60, per="user")),
    ],
)
async def send_message(
    conversation_id: uuid.UUID,
    payload: MessageCreateRequest,
    background_tasks: BackgroundTasks,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Sends a user message and returns the Companion's reply. See
    app/modules/companion/service.py for exactly what context is
    (and isn't yet) fed to the LLM."""
    service = CompanionService(db)
    conversation = await service.get_owned_conversation(conversation_id=conversation_id, user_id=current_user.id)
    reply = await service.send_message(conversation=conversation, user=current_user, content=payload.content, defer_post_turn=True)
    # Summarization and memory extraction are extra model calls that do not change this reply: run them after the
    # response has been sent, on their own database session (the request's session closes with the response).
    background_tasks.add_task(_post_turn, conversation_id, current_user.id, payload.content, reply.content)
    return MessageResponse.model_validate(reply)


@router.get("/conversations/{conversation_id}/changes", response_model=list[PendingChangeResponse])
async def list_pending_changes(
    conversation_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Lists AI-proposed itinerary changes for this conversation
    (pending, confirmed, and rejected — newest first)."""
    from app.modules.companion.change_service import PendingChangeService

    await CompanionService(db).get_owned_conversation(conversation_id=conversation_id, user_id=current_user.id)
    changes = await PendingChangeService(db).list_for_conversation(conversation_id)
    return [PendingChangeResponse.model_validate(c) for c in changes]


@router.post("/changes/{change_id}/confirm", response_model=ConfirmChangeResult, dependencies=[Depends(require_feature(FeatureFlag.PLANNER))])
async def confirm_change(
    change_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Applies an AI-proposed change. Requires editor/owner access to
    the trip — re-verified fresh here, independent of who proposed it
    or who owns the conversation (Blueprint §40, Principle 2)."""
    from app.modules.companion.change_service import PendingChangeService

    change, result = await PendingChangeService(db).confirm(change_id=change_id, actor_id=current_user.id)
    return ConfirmChangeResult(change=PendingChangeResponse.model_validate(change), applied=result)


@router.post("/changes/{change_id}/reject", response_model=PendingChangeResponse)
async def reject_change(
    change_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    from app.modules.companion.change_service import PendingChangeService

    change = await PendingChangeService(db).reject(change_id=change_id, actor_id=current_user.id)
    return PendingChangeResponse.model_validate(change)


@router.post(
    "/messages/{message_id}/speech",
    dependencies=[Depends(require_feature(FeatureFlag.COMPANION)), Depends(require_feature(FeatureFlag.VOICE)),
                  Depends(rate_limit(bucket="companion:speech", max_requests=60, window_seconds=3600, per="user"))],
    responses={200: {"content": {"audio/mpeg": {}}, "description": "The reply, spoken (MP3)."}},
)
async def speak_message(
    message_id: uuid.UUID, current_user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)
):
    """Reads one of your own assistant replies aloud and returns MP3 audio. Generated on demand and never stored.
    503 `provider_unavailable` when no speech provider is configured (the app then falls back to the browser's voice)."""
    audio, content_type = await SpeechService(db).speak_message(message_id=message_id, user_id=current_user.id)
    return Response(content=audio, media_type=content_type, headers={"Cache-Control": "private, max-age=3600"})


@router.post(
    "/conversations/{conversation_id}/voice-messages", response_model=VoiceMessageResponse,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(require_feature(FeatureFlag.COMPANION)), 
        Depends(require_feature(FeatureFlag.VOICE)),
        Depends(rate_limit(bucket="companion:voice", max_requests=10, window_seconds=60, per="user")),
    ],
)
async def send_voice_message(
    conversation_id: uuid.UUID,
    file: UploadFile = File(...),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Master Blueprint §41: audio -> transcription -> Companion ->
    response. Gated on the VOICE feature flag; raw audio is never
    persisted (see VoiceService docstring)."""
    from app.modules.companion.voice_service import VoiceService

    audio_bytes = await file.read()
    transcript, reply = await VoiceService(db).transcribe_and_send(
        conversation_id=conversation_id, user=current_user, audio_bytes=audio_bytes,
        content_type=file.content_type or "audio/mpeg", filename=file.filename or "voice.mp3",
    )
    return VoiceMessageResponse(transcript=transcript, reply=MessageResponse.model_validate(reply))
