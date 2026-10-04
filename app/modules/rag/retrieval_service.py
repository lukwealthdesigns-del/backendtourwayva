"""
RAGRetrievalService - Query -> embedding -> similarity search ->
metadata filtering -> reranking -> context construction (Master
Blueprint section 33) — now implementing all five steps.

Reranking here is a lightweight HYBRID approach, not a separate
reranking model/API call: it over-fetches candidates by pure vector
similarity, then re-scores each by blending cosine similarity with a
simple lexical (word-overlap) score against the query, and returns
the top_k by that blended score. This catches cases where a chunk is
semantically "in the neighborhood" but doesn't actually share any of
the query's specific terms — a known weak spot of similarity-only
retrieval — without requiring a second model call per search.
"""
from __future__ import annotations

import re
from typing import Optional

from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.rag.schemas import KnowledgeSearchResponse, KnowledgeSearchResult
from app.providers.embeddings.openai_embeddings_provider import OpenAIEmbeddingProvider
from app.repositories.knowledge_repository import KnowledgeRepository

_embeddings = OpenAIEmbeddingProvider()

# How many extra candidates to over-fetch for reranking, as a
# multiple of top_k. Higher = better chance of catching a lexically
# strong but semantically-middling match, at the cost of one larger
# vector search.
_OVERFETCH_MULTIPLIER = 4

# Blend weight: how much the final score favors vector similarity
# over lexical overlap. Vector similarity remains dominant — lexical
# overlap is a tie-breaker/correction, not a replacement.
_SIMILARITY_WEIGHT = 0.75
_LEXICAL_WEIGHT = 1 - _SIMILARITY_WEIGHT

_WORD_RE = re.compile(r"[a-z0-9]+")


def _tokenize(text: str) -> set[str]:
    return set(_WORD_RE.findall(text.lower()))


def _lexical_overlap_score(query_tokens: set[str], chunk_text: str) -> float:
    """Jaccard-style overlap: fraction of query tokens that appear in
    the chunk. Deliberately asymmetric (denominator is query length,
    not union) — we care whether the chunk covers what was asked,
    not how much of the (usually much longer) chunk matches back."""
    if not query_tokens:
        return 0.0
    chunk_tokens = _tokenize(chunk_text)
    if not chunk_tokens:
        return 0.0
    overlap = len(query_tokens & chunk_tokens)
    return overlap / len(query_tokens)


class RAGRetrievalService:
    def __init__(self, db: AsyncSession):
        self.db = db
        self.repo = KnowledgeRepository(db)

    async def search(
        self, query: str, *, top_k: int = 5, source_filter: Optional[str] = None
    ) -> KnowledgeSearchResponse:
        [query_embedding] = await _embeddings.embed([query])

        candidates = await self.repo.similarity_search(
            query_embedding, top_k=top_k * _OVERFETCH_MULTIPLIER, source_filter=source_filter
        )
        if not candidates:
            return KnowledgeSearchResponse(results=[], count=0)

        query_tokens = _tokenize(query)
        scored = []
        for chunk, distance in candidates:
            similarity = 1.0 - distance  # cosine distance -> similarity, roughly in [0, 2] -> usually [0, 1]
            lexical = _lexical_overlap_score(query_tokens, chunk.chunk_text)
            blended = (_SIMILARITY_WEIGHT * similarity) + (_LEXICAL_WEIGHT * lexical)
            scored.append((blended, chunk))

        scored.sort(key=lambda pair: pair[0], reverse=True)
        top_chunks = [chunk for _, chunk in scored[:top_k]]

        results = []
        for chunk in top_chunks:
            document = await self.repo.get_document(chunk.document_id)
            results.append(
                KnowledgeSearchResult(
                    chunk_id=chunk.id,
                    document_id=chunk.document_id,
                    document_title=document.title if document else "Unknown",
                    chunk_text=chunk.chunk_text,
                )
            )
        return KnowledgeSearchResponse(results=results, count=len(results))
