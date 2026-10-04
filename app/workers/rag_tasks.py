"""Knowledge-base ingestion task (Master Prompt §33, §69)."""
from __future__ import annotations

import asyncio
import uuid

from app.core.exceptions import ProviderUnavailableError
from app.db.session import AsyncSessionLocal
from app.workers.celery_app import celery_app


@celery_app.task(
    name="rag.ingest",
    bind=True,
    acks_late=True,
    ignore_result=True,
    autoretry_for=(ProviderUnavailableError,),
    retry_backoff=15,
    retry_backoff_max=900,
    retry_jitter=True,
    max_retries=5,
    soft_time_limit=300,
    time_limit=360,
)
def ingest_document_task(self, document_id: str) -> str:
    try:
        return asyncio.run(_run(document_id))
    except ProviderUnavailableError:
        if self.request.retries >= self.max_retries:
            asyncio.run(_give_up(document_id))
        raise


async def _run(document_id: str) -> str:
    from app.modules.rag.ingestion_service import RAGIngestionService

    async with AsyncSessionLocal() as db:
        return await RAGIngestionService(db).process(uuid.UUID(document_id))


async def _give_up(document_id: str) -> None:
    from app.modules.rag.ingestion_service import RAGIngestionService

    async with AsyncSessionLocal() as db:
        await RAGIngestionService(db).mark_failed(uuid.UUID(document_id), "Embedding provider unavailable after retries.")
