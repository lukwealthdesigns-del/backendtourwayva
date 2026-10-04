"""Knowledge document/chunk repository, including the pgvector
cosine-distance similarity search that powers RAG retrieval."""
from __future__ import annotations

import uuid
from typing import Optional, Sequence

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models.knowledge import KnowledgeChunk, KnowledgeDocument


class KnowledgeRepository:
    def __init__(self, db: AsyncSession):
        self.db = db

    async def create_document(self, document: KnowledgeDocument) -> KnowledgeDocument:
        self.db.add(document)
        await self.db.flush()
        return document

    async def get_document(self, document_id: uuid.UUID) -> Optional[KnowledgeDocument]:
        result = await self.db.execute(select(KnowledgeDocument).where(KnowledgeDocument.id == document_id))
        return result.scalar_one_or_none()

    async def get_document_for_update(self, document_id: uuid.UUID) -> Optional[KnowledgeDocument]:
        result = await self.db.execute(
            select(KnowledgeDocument).where(KnowledgeDocument.id == document_id).with_for_update()
        )
        return result.scalar_one_or_none()

    async def save_document(self, document: KnowledgeDocument) -> KnowledgeDocument:
        await self.db.flush()
        return document

    async def add_chunk(self, chunk: KnowledgeChunk) -> KnowledgeChunk:
        self.db.add(chunk)
        await self.db.flush()
        return chunk

    async def similarity_search(
        self,
        query_embedding: list[float],
        *,
        top_k: int = 5,
        source_filter: Optional[str] = None,
    ) -> Sequence[tuple[KnowledgeChunk, float]]:
        """Nearest-neighbor search by cosine distance (pgvector's
        `<=>` operator via the `cosine_distance` comparator) —
        ascending distance = descending similarity, so the closest
        matches come first. Returns (chunk, distance) pairs so callers
        (RAGRetrievalService) can rerank using the raw distance rather
        than just result order.

        `source_filter`, when given, restricts candidates to
        KnowledgeDocument.source == source_filter (Blueprint §33's
        "metadata filtering" pipeline step) — e.g. only "admin_curated"
        documents, excluding ad hoc "manual" uploads.
        """
        distance_col = KnowledgeChunk.embedding.cosine_distance(query_embedding)
        stmt = select(KnowledgeChunk, distance_col.label("distance"))

        if source_filter:
            stmt = stmt.join(KnowledgeDocument, KnowledgeDocument.id == KnowledgeChunk.document_id).where(
                KnowledgeDocument.source == source_filter
            )

        stmt = stmt.order_by(distance_col).limit(top_k)
        result = await self.db.execute(stmt)
        return [(row[0], float(row[1])) for row in result.all()]
