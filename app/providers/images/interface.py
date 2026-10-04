"""Image provider abstraction (Master Blueprint §31-32)."""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Optional


@dataclass(frozen=True)
class ImageResult:
    url: str
    thumbnail_url: str
    width: int
    height: int
    photographer_name: str
    photographer_profile_url: str
    source: str            # e.g. "unsplash"
    attribution_required: bool = True
    provider: str = "unsplash"


class ImageProvider(ABC):
    @abstractmethod
    async def search(self, query: str) -> Optional[ImageResult]:
        """Return the best-matching image for a query, or None if
        nothing relevant was found. Never fabricate an image URL."""
        raise NotImplementedError
