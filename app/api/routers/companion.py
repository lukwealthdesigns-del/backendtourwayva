"""Companion endpoints (Master Blueprint §35) — scope caveats are
documented in app/modules/companion/service.py and __init__.py."""
from __future__ import annotations

import uuid

from fastapi import APIRouter, BackgroundTasks, Depends, File, UploadFile, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies import get_current_user, rate_limit, require_feature
from app.core.constants import FeatureFlag
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
