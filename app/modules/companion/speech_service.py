"""Spoken replies: turns one of the caller's own assistant messages into audio.

Only text that already exists in the caller's own conversation can be spoken (a message id, never arbitrary text), so
the endpoint cannot be used as a free text-to-speech API. Markdown is reduced to plain speech and the length is capped
to bound cost."""
from __future__ import annotations

import re
import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.constants import MessageRole
from app.core.exceptions import NotFoundError
from app.db.models.conversation import Message
from app.modules.companion.service import CompanionService
from app.providers.text_to_speech.interface import TextToSpeechProvider
from app.providers.text_to_speech.openai_tts_provider import OpenAITTSProvider


def plain_speech(markdown: str, limit: int) -> str:
    """Markdown -> plain text suitable for reading aloud (no symbols read out as 'asterisk', links keep their label)."""
    s = re.sub(r"```.*?```", " ", markdown or "", flags=re.S)
    s = re.sub(r"`([^`]*)`", r"\1", s)
    s = re.sub(r"!\[[^\]]*\]\([^)]*\)", " ", s)
    s = re.sub(r"\[([^\]]+)\]\([^)]*\)", r"\1", s)
    s = re.sub(r"^\s{0,3}#{1,6}\s*", "", s, flags=re.M)
    s = re.sub(r"^\s*[-*+]\s+|^\s*\d+[.)]\s+|^\s*>\s?", "", s, flags=re.M)
    s = re.sub(r"(\*\*|__|\*|_|~~)", "", s)
    s = re.sub(r"\|", " ", s)
    s = re.sub(r"[ \t]+", " ", s)
    s = re.sub(r"\n{2,}", ". ", s).replace("\n", " ").strip()
    if len(s) > limit:
        cut = s[:limit]
        end = max(cut.rfind(". "), cut.rfind("? "), cut.rfind("! "))
        s = (cut[: end + 1] if end > limit // 2 else cut).strip()
    return s


class SpeechService:
    def __init__(self, db: AsyncSession, provider: TextToSpeechProvider | None = None):
        self.db = db
        self.companion = CompanionService(db)
        self.provider = provider or OpenAITTSProvider()

    async def speak_message(self, *, message_id: uuid.UUID, user_id: uuid.UUID) -> tuple[bytes, str]:
        message = await self.db.get(Message, message_id)
        # Unknown id, someone else's message and non-assistant messages all look the same: 404.
        if message is None or message.role != MessageRole.ASSISTANT:
            raise NotFoundError("Message not found.")
        await self.companion.get_owned_conversation(conversation_id=message.conversation_id, user_id=user_id)
        text = plain_speech(message.content, settings.TTS_MAX_CHARS)
        if not text:
            raise NotFoundError("Nothing to read aloud.")
        return await self.provider.synthesize(text), self.provider.content_type
