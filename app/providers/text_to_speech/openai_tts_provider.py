"""OpenAI text-to-speech (`/v1/audio/speech`). Same account/credential as the LLM and Whisper providers.

The audio is generated on demand and streamed straight back to the caller; it is never written to storage (Blueprint §41:
do not retain audio unless explicitly required and permitted)."""
from __future__ import annotations

import httpx

from app.core.config import settings
from app.core.exceptions import ProviderUnavailableError
from app.core.logging import get_logger
from app.core.redaction import redact_secrets
from app.core.resilience import CircuitOpenError
from app.providers.http_resilience import resilient_request
from app.providers.text_to_speech.interface import TextToSpeechProvider

logger = get_logger(__name__)

_SPEECH_URL = "https://api.openai.com/v1/audio/speech"


class OpenAITTSProvider(TextToSpeechProvider):
    async def synthesize(self, text: str) -> bytes:
        if not settings.OPENAI_API_KEY:
            raise ProviderUnavailableError("Spoken replies are not configured.")
        headers = {"Authorization": f"Bearer {settings.OPENAI_API_KEY}"}
        body = {"model": settings.TTS_MODEL, "voice": settings.TTS_VOICE, "input": text, "response_format": "mp3"}

        async def attempt() -> bytes:
            async with httpx.AsyncClient(timeout=45.0) as client:
                resp = await client.post(_SPEECH_URL, headers=headers, json=body)
                resp.raise_for_status()
                return resp.content

        try:
            audio = await resilient_request("openai_tts", attempt)
        except CircuitOpenError:
            raise
        except Exception as exc:  # noqa: BLE001
            logger.error("tts_failed", error=redact_secrets(exc))
            raise ProviderUnavailableError("Couldn't generate the spoken reply. Please try again.") from exc
        if not audio:
            raise ProviderUnavailableError("Couldn't generate the spoken reply. Please try again.")
        return audio
