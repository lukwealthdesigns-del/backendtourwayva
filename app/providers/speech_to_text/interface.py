"""Speech-to-text provider abstraction (Master Blueprint §41)."""
from __future__ import annotations

from abc import ABC, abstractmethod


class SpeechToTextProvider(ABC):
    @abstractmethod
    async def transcribe(self, audio_bytes: bytes, content_type: str, filename: str) -> str:
        """Transcribe audio to text. Returns an empty string (never
        fabricated content) if transcription fails or produces
        nothing usable."""
        raise NotImplementedError
