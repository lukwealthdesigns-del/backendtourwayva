"""
OpenAI embeddings provider — used by the future RAG pipeline
(Blueprint §33-34, scaffolded — not wired into Companion yet in this
delivery; see app/modules/rag).

Kept as a standalone provider now so Memory or Companion features
that want semantic similarity later don't need a new abstraction.
"""
from __future__ import annotations

import httpx

from app.core.config import settings
from app.core.redaction import redact_secrets
from app.core.resilience import CircuitOpenError
from app.providers.http_resilience import resilient_request
from app.core.exceptions import ProviderUnavailableError
from app.core.logging import get_logger
from app.providers.embeddings.interface import EmbeddingProvider

logger = get_logger(__name__)

_EMBEDDINGS_URL = "https://api.openai.com/v1/embeddings"


class OpenAIEmbeddingProvider(EmbeddingProvider):
    async def embed(self, texts: list[str]) -> list[list[float]]:
        if not settings.OPENAI_API_KEY:
            raise ProviderUnavailableError("Embeddings provider is not configured.")

        headers = {
            "Authorization": f"Bearer {settings.OPENAI_API_KEY}",
            "Content-Type": "application/json",
        }
        body = {"model": settings.AI_EMBEDDING_MODEL, "input": texts}

        async def attempt() -> dict:
            async with httpx.AsyncClient(timeout=20.0) as client:
                resp = await client.post(_EMBEDDINGS_URL, json=body, headers=headers)
                resp.raise_for_status()
                return resp.json()

        try:
            data = await resilient_request("openai_embeddings", attempt)
        except CircuitOpenError:
            raise
        except Exception as exc:  # noqa: BLE001
            logger.error("openai_embeddings_failed", error=redact_secrets(exc))
            raise ProviderUnavailableError("Embedding generation failed.") from exc

        # OpenAI preserves input order in `data`, but sort by `index`
        # defensively rather than assuming it.
        ordered = sorted(data["data"], key=lambda d: d["index"])
        return [d["embedding"] for d in ordered]
