"""Embeddings provider abstraction (Master Blueprint §33)."""
from __future__ import annotations

from abc import ABC, abstractmethod


class EmbeddingProvider(ABC):
    @abstractmethod
    async def embed(self, texts: list[str]) -> list[list[float]]:
        """Return one embedding vector per input text, same order.
        Never fabricate a vector — raise ProviderUnavailableError on
        failure or misconfiguration."""
        raise NotImplementedError
