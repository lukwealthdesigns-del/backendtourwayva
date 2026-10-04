"""
RAG knowledge models (Master Blueprint §33-34).

KnowledgeDocument is the source text (e.g. a travel guide article,
an admin-curated destination brief); KnowledgeChunk is one embedded,
searchable slice of it. Retrieval runs a pgvector cosine-distance
search over KnowledgeChunk.embedding.

Scoped for this delivery: this is SHARED knowledge only (Blueprint
§34: "Never mix private user data into global shared retrieval") —
there is no per-user knowledge base here. User-private RAG (trips,
conversations, memories, uploaded documents as retrievable context)
is a further increment once there's a concrete need for it beyond
what the existing tool system already exposes directly.

Embedding dimension is fixed at 1536 to match OpenAI's
`text-embedding-3-small` (the AI_EMBEDDING_MODEL default in
app/core/config.py). Changing AI_EMBEDDING_MODEL to a model with a
different output dimension requires a new migration altering this
column — it is NOT dynamically sized.
"""
from __future__ import annotations

import uuid
from typing import Optional

from pgvector.sqlalchemy import Vector
from sqlalchemy import ForeignKey, Integer, String, Text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin, UUIDPKMixin

EMBEDDING_DIMENSIONS = 1536


class KnowledgeDocument(UUIDPKMixin, TimestampMixin, Base):
    __tablename__ = "knowledge_documents"

    title: Mapped[str] = mapped_column(String(255), nullable=False)
    source: Mapped[str] = mapped_column(String(255), nullable=False)  # e.g. "manual", "admin_curated"
    content: Mapped[str] = mapped_column(Text, nullable=False)
    # Ingestion runs in a worker: "processing" -> "ready" | "failed" (Master Prompt §69).
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="ready")
    chunk_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    error: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)


class KnowledgeChunk(UUIDPKMixin, TimestampMixin, Base):
    __tablename__ = "knowledge_chunks"

    document_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("knowledge_documents.id", ondelete="CASCADE"), nullable=False, index=True
    )
    chunk_index: Mapped[int] = mapped_column(Integer, nullable=False)
    chunk_text: Mapped[str] = mapped_column(Text, nullable=False)
    embedding: Mapped[list[float]] = mapped_column(Vector(EMBEDDING_DIMENSIONS), nullable=False)
