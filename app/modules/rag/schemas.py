"""Pydantic schemas for RAG endpoints (Master Blueprint §33-34)."""
from __future__ import annotations

import uuid
from datetime import datetime

from typing import Optional

from pydantic import BaseModel, Field


class DocumentIngestRequest(BaseModel):
    """Admin-only (`content:manage`): shared knowledge feeds every user's Companion."""

    title: str = Field(..., min_length=1, max_length=255)
    source: str = Field(default="manual", max_length=255)
    content: str = Field(..., min_length=1, max_length=50_000)


class DocumentIngestResponse(BaseModel):
    document_id: uuid.UUID
    title: str
    status: str = "ready"        # "processing" (queued/embedding) | "ready" | "failed"
    chunk_count: int = 0
    error: Optional[str] = None


class KnowledgeSearchResult(BaseModel):
    chunk_id: uuid.UUID
    document_id: uuid.UUID
    document_title: str
    chunk_text: str


class KnowledgeSearchResponse(BaseModel):
    results: list[KnowledgeSearchResult]
    count: int


class KnowledgeDocumentSummary(BaseModel):
    """One document in the admin knowledge list (the full text is not sent: only a short preview)."""

    document_id: uuid.UUID
    title: str
    source: str
    status: str
    chunk_count: int = 0
    error: Optional[str] = None
    content_chars: int = 0
    preview: str = ""
    created_at: datetime


class KnowledgeDocumentList(BaseModel):
    items: list[KnowledgeDocumentSummary]
    total: int
    limit: int
    offset: int
