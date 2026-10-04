"""
OpenAI Whisper speech-to-text provider.

Chosen because no STT provider was named in the Master Build Prompt,
and OpenAI is already the configured account for LLM/embeddings
(OPENAI_API_KEY) — using the same provider avoids introducing a new
credential just for this. Uses the `/v1/audio/transcriptions`
endpoint directly via httpx multipart upload.

Per Blueprint §41 ("Do not permanently retain audio unless explicitly
required and permitted"), this provider never writes the audio to
storage — the caller (VoiceService) passes raw bytes in memory and
they're discarded after this call returns.
"""
from __future__ import annotations

import httpx

from app.core.config import settings
from app.core.redaction import redact_secrets
from app.core.resilience import CircuitOpenError
from app.providers.http_resilience import resilient_request
from app.core.exceptions import ProviderUnavailableError
from app.core.logging import get_logger
from app.providers.speech_to_text.interface import SpeechToTextProvider

logger = get_logger(__name__)

_TRANSCRIPTION_URL = "https://api.openai.com/v1/audio/transcriptions"


class OpenAIWhisperProvider(SpeechToTextProvider):
    async def transcribe(self, audio_bytes: bytes, content_type: str, filename: str) -> str:
        if not settings.OPENAI_API_KEY:
            raise ProviderUnavailableError("Voice transcription provider is not configured.")

        headers = {"Authorization": f"Bearer {settings.OPENAI_API_KEY}"}
        files = {"file": (filename, audio_bytes, content_type)}
        data = {"model": "whisper-1"}

        async def attempt() -> str:
            async with httpx.AsyncClient(timeout=30.0) as client:
                resp = await client.post(_TRANSCRIPTION_URL, headers=headers, files=files, data=data)
                resp.raise_for_status()
                return resp.json().get("text", "").strip()

        try:
            # Uploading audio bytes again on retry is fine (no side effect on OpenAI's end);
            # transcription is naturally idempotent.
            return await resilient_request("openai_whisper", attempt)
        except CircuitOpenError:
            raise
        except Exception as exc:  # noqa: BLE001
            logger.error("whisper_transcription_failed", error=redact_secrets(exc))
            raise ProviderUnavailableError("Voice transcription failed. Please try again or type your message.") from exc
