"""RAG knowledge-base endpoints (Master Blueprint §33-34)."""
from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, Query, Response, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies import get_current_user, require_admin_permission, rate_limit
from app.core.exceptions import NotFoundError
from app.db.models.user import User
from app.db.session import get_db
from app.modules.rag.ingestion_service import RAGIngestionService
from app.modules.rag.retrieval_service import RAGRetrievalService
from app.modules.rag.schemas import (
    DocumentIngestRequest,
    DocumentIngestResponse,
    KnowledgeSearchResponse,
)

router = APIRouter(prefix="/rag", tags=["Knowledge Base"])


def _document_response(document) -> DocumentIngestResponse:
    return DocumentIngestResponse(
        document_id=document.id, title=document.title, status=document.status,
        chunk_count=document.chunk_count, error=document.error,
    )


@router.post(
    "/documents", response_model=DocumentIngestResponse,
    dependencies=[Depends(require_admin_permission("content:manage"))],
)
async def ingest_document(
    payload: DocumentIngestRequest,
    response: Response,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Requires `content:manage`. Chunking and embedding run in a worker: in production this
    returns **202** with status "processing" — poll `GET /rag/documents/{id}` until "ready".
    (In development it runs inline and returns the finished document.)"""
    document = await RAGIngestionService(db).submit(payload)
    response.status_code = status.HTTP_202_ACCEPTED if document.status == "processing" else status.HTTP_201_CREATED
    return _document_response(document)


@router.get(
    "/documents/{document_id}", response_model=DocumentIngestResponse,
    dependencies=[Depends(require_admin_permission("content:manage"))],
)
async def get_document(document_id: uuid.UUID, db: AsyncSession = Depends(get_db)):
    document = await RAGIngestionService(db).get(document_id)
    if document is None:
        raise NotFoundError("Document not found.")
    return _document_response(document)


@router.get("/search", response_model=KnowledgeSearchResponse, dependencies=[Depends(rate_limit(bucket="rag:search", max_requests=30, window_seconds=300, per="user"))])
async def search_knowledge(
    query: str = Query(..., min_length=1, max_length=500),
    top_k: int = Query(default=5, ge=1, le=20),
    source: str | None = Query(default=None, description="Optional metadata filter, e.g. 'admin_curated'"),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    return await RAGRetrievalService(db).search(query, top_k=top_k, source_filter=source)
