"""
VoiceService (Master Blueprint §41): Audio -> secure upload
(validated in-memory, never persisted) -> validation -> speech-to-text
-> text -> Companion -> response.

Reuses the entire existing CompanionService.send_message pipeline
(tool-calling, memory injection, summarization, AI-usage recording)
once the audio is transcribed to text — voice is just a different
front door into the same Companion, not a separate response path.
"""
from __future__ import annotations

import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import ValidationAppError
from app.db.models.conversation import Message
from app.db.models.user import User
from app.modules.companion.service import CompanionService
from app.providers.speech_to_text.openai_whisper_provider import OpenAIWhisperProvider

_ALLOWED_AUDIO_MIMES = {"audio/mpeg", "audio/mp4", "audio/wav", "audio/webm", "audio/ogg", "audio/x-m4a"}
_MAX_AUDIO_SIZE_MB = 25

_provider = OpenAIWhisperProvider()


class VoiceService:
    def __init__(self, db: AsyncSession):
        self.db = db
        self.companion_service = CompanionService(db)

    async def transcribe_and_send(
        self, *, conversation_id: uuid.UUID, user: User, audio_bytes: bytes, content_type: str, filename: str
    ) -> tuple[str, Message]:
        if content_type not in _ALLOWED_AUDIO_MIMES:
            raise ValidationAppError(f"Unsupported audio format '{content_type}'.")
        if len(audio_bytes) > _MAX_AUDIO_SIZE_MB * 1024 * 1024:
            raise ValidationAppError(f"Audio exceeds the {_MAX_AUDIO_SIZE_MB}MB size limit.")

        transcript = await _provider.transcribe(audio_bytes, content_type, filename)
        if not transcript:
            raise ValidationAppError("Could not understand the audio. Please try again or type your message.")

        conversation = await self.companion_service.get_owned_conversation(
            conversation_id=conversation_id, user_id=user.id
        )
        reply = await self.companion_service.send_message(conversation=conversation, user=user, content=transcript)

        # audio_bytes goes out of scope here and is never written to
        # disk or object storage anywhere in this path.
        return transcript, reply
