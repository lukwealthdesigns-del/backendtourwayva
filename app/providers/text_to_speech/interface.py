"""Text-to-speech provider abstraction (Master Blueprint §2: provider abstraction for every external service)."""
from __future__ import annotations

from abc import ABC, abstractmethod


class TextToSpeechProvider(ABC):
    content_type: str = "audio/mpeg"

    @abstractmethod
    async def synthesize(self, text: str) -> bytes:
        """Return spoken audio for `text`. Raises ProviderUnavailableError when the provider is not configured or
        fails; never returns fabricated/empty audio."""
        raise NotImplementedError
