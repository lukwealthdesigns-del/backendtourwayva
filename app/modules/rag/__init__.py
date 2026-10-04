"""RAG module - IMPLEMENTED (Phase 4, complete).
See chunking.py (paragraph-based chunker), ingestion_service.py
(RAGIngestionService: chunk -> embed -> store), and
retrieval_service.py (RAGRetrievalService: embed query -> pgvector
cosine similarity search -> optional metadata filtering (by
KnowledgeDocument.source) -> lightweight hybrid reranking (blends
vector similarity with lexical word-overlap against the query)).

Wired into Companion as the `search_travel_knowledge` tool (see
app/modules/companion/tools.py) - the model decides when to use it,
same as every other tool.

Still shared-knowledge-only, per Blueprint section 34 (no user-private
retrieval - trips/conversations/memories aren't part of this index).
Document ingestion (POST /rag/documents) is temporarily open to any
authenticated user, same caveat as app/modules/places - will become
admin-gated once Phase 7 (Admin/RBAC) exists."""
