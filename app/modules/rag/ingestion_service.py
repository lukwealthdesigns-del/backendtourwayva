"""
RAGIngestionService — Document -> cleaning -> chunking -> embeddings -> pgvector
(Master Blueprint §33), run in a WORKER (Master Prompt §69).

  submit()   validates nothing more than the admin already did, stores the document
             with status "processing" and returns at once; in production the work is
             queued (`rag.ingest`), in development it runs inline
  process()  chunks the text, embeds it in batches, stores the chunks, and flips the
             document to "ready" — or "failed" with a short reason. A TRANSIENT embedding
             outage propagates so the task retries; nothing is half-written because the
             chunks and the status change commit together.

"Cleaning" is limited to the whitespace normalization inside chunk_text().
"""
from __future__ import annotations

import asyncio
import uuid
from typing import Optional

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.exceptions import ProviderUnavailableError
from app.core.logging import get_logger
from app.db.models.knowledge import KnowledgeChunk, KnowledgeDocument
from app.modules.rag.chunking import chunk_text
from app.modules.rag.schemas import DocumentIngestRequest
from app.providers.embeddings.openai_embeddings_provider import OpenAIEmbeddingProvider
from app.repositories.knowledge_repository import KnowledgeRepository

logger = get_logger(__name__)

PROCESSING, READY, FAILED = "processing", "ready", "failed"
EMBED_BATCH_SIZE = 32


class RAGIngestionService:
    def __init__(self, db: AsyncSession, embeddings: Optional[OpenAIEmbeddingProvider] = None):
        self.db = db
        self.repo = KnowledgeRepository(db)
        self.embeddings = embeddings or OpenAIEmbeddingProvider()

    async def submit(self, payload: DocumentIngestRequest) -> KnowledgeDocument:
        document = KnowledgeDocument(
            title=payload.title, source=payload.source, content=payload.content, status=PROCESSING, chunk_count=0
        )
        await self.repo.create_document(document)
        await self.db.commit()

        if settings.tasks_execution == "background":
            try:
                from app.workers.celery_app import celery_app

                await asyncio.to_thread(celery_app.send_task, "rag.ingest", args=[str(document.id)])
                return document
            except Exception as exc:  # noqa: BLE001
                logger.error("rag_enqueue_failed", error=str(exc))     # broker down: do the work here instead
        await self.process(document.id)
        return document

    async def process(self, document_id: uuid.UUID) -> str:
        """Returns "ready" | "failed" | "skipped" | "missing"."""
        document = await self.repo.get_document_for_update(document_id)
        if document is None:
            return "missing"
        if document.status != PROCESSING:
            return "skipped"

        chunks = chunk_text(document.content)
        try:
            vectors: list[list[float]] = []
            for start in range(0, len(chunks), EMBED_BATCH_SIZE):
                vectors.extend(await self.embeddings.embed(chunks[start : start + EMBED_BATCH_SIZE]))
        except ProviderUnavailableError:
            await self.db.rollback()
            raise                                         # transient: let the task retry
        except Exception as exc:  # noqa: BLE001
            logger.error("rag_ingestion_failed", document_id=str(document_id), error=str(exc))
            document.status, document.error = FAILED, "Embedding failed."
            await self.repo.save_document(document)
            await self.db.commit()
            return FAILED

        for index, (text, vector) in enumerate(zip(chunks, vectors)):
            await self.repo.add_chunk(
                KnowledgeChunk(document_id=document.id, chunk_index=index, chunk_text=text, embedding=vector)
            )
        document.status, document.chunk_count, document.error = READY, len(chunks), None
        await self.repo.save_document(document)
        await self.db.commit()
        return READY

    async def mark_failed(self, document_id: uuid.UUID, reason: str) -> None:
        """The task has exhausted its retries."""
        document = await self.repo.get_document_for_update(document_id)
        if document is not None and document.status == PROCESSING:
            document.status, document.error = FAILED, reason[:255]
            await self.repo.save_document(document)
            await self.db.commit()

    async def get(self, document_id: uuid.UUID) -> Optional[KnowledgeDocument]:
        return await self.repo.get_document(document_id)
