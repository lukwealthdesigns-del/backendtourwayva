"""Text-extraction provider abstraction (Master Blueprint §42:
"extraction/OCR" step of the attachment pipeline)."""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass


@dataclass(frozen=True)
class ExtractionResult:
    text: str
    provider: str


class TextExtractionProvider(ABC):
    @abstractmethod
    async def extract(self, file_bytes: bytes, content_type: str) -> ExtractionResult:
        """Extract plain text from a file. Returns an empty string
        (never fabricated content) if nothing could be extracted —
        e.g. an unsupported format or a blank scan."""
        raise NotImplementedError
