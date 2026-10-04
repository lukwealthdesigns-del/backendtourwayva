"""Knowledge-base ingestion as a worker job: status tracking, batching, retry semantics."""
from __future__ import annotations

import asyncio
import uuid
from types import SimpleNamespace as NS

import pytest

from app.core.config import settings
from app.core.exceptions import ProviderUnavailableError
from app.modules.rag import ingestion_service as ingestion_module
from app.modules.rag.ingestion_service import EMBED_BATCH_SIZE, FAILED, PROCESSING, READY, RAGIngestionService
from app.modules.rag.schemas import DocumentIngestRequest


class _FakeDB:
    def __init__(self):
        self.commits = 0
        self.rollbacks = 0

    async def commit(self):
        self.commits += 1

    async def rollback(self):
        self.rollbacks += 1


class _Repo:
    def __init__(self):
        self.documents = {}
        self.chunks = []

    async def create_document(self, document):
        document.id = uuid.uuid4()
        self.documents[document.id] = document
        return document

    async def get_document_for_update(self, document_id):
        return self.documents.get(document_id)

    async def get_document(self, document_id):
        return self.documents.get(document_id)

    async def save_document(self, document):
        return document

    async def add_chunk(self, chunk):
        self.chunks.append(chunk)


class _Embeddings:
    def __init__(self, error=None):
        self.error, self.batches = error, []

    async def embed(self, texts):
        self.batches.append(len(texts))
        if self.error:
            raise self.error
        return [[0.1, 0.2] for _ in texts]


def _service(embeddings=None):
    service = RAGIngestionService.__new__(RAGIngestionService)
    service.db, service.repo, service.embeddings = _FakeDB(), _Repo(), embeddings or _Embeddings()
    return service


def _payload(paragraphs=3):
    return DocumentIngestRequest(title="Visa rules", source="admin_curated",
                                 content="\n\n".join(f"Paragraph {n}. " + "x" * 900 for n in range(paragraphs)))


def _run(coro):
    return asyncio.run(coro)


def test_submit_returns_immediately_in_production_and_leaves_the_work_to_the_worker(monkeypatch):
    monkeypatch.setattr(settings, "TASKS_EXECUTION", "background")
    sent = []
    monkeypatch.setattr("app.workers.celery_app.celery_app.send_task", lambda name, args=None, **k: sent.append((name, args)))
    service = _service()
    document = _run(service.submit(_payload()))
    assert document.status == PROCESSING and document.chunk_count == 0
    assert sent == [("rag.ingest", [str(document.id)])] and service.repo.chunks == []
    assert service.embeddings.batches == []                                   # no embedding call in the request


def test_in_development_the_document_is_processed_inline(monkeypatch):
    monkeypatch.setattr(settings, "TASKS_EXECUTION", "inline")
    service = _service()
    document = _run(service.submit(_payload(3)))
    assert document.status == READY and document.chunk_count == 3 and len(service.repo.chunks) == 3


def test_a_dead_broker_falls_back_to_inline_processing(monkeypatch):
    monkeypatch.setattr(settings, "TASKS_EXECUTION", "background")

    def boom(*a, **k):
        raise ConnectionError("broker down")

    monkeypatch.setattr("app.workers.celery_app.celery_app.send_task", boom)
    service = _service()
    assert _run(service.submit(_payload())).status == READY


def test_process_chunks_embeds_in_bounded_batches_and_marks_ready():
    service = _service()
    document = _run(service.repo.create_document(NS(title="t", source="s", status=PROCESSING, chunk_count=0, error=None,
                                                     content="\n\n".join("p" * 900 for _ in range(70)))))
    assert _run(service.process(document.id)) == READY
    assert sum(service.embeddings.batches) == document.chunk_count == len(service.repo.chunks)
    assert max(service.embeddings.batches) <= EMBED_BATCH_SIZE and len(service.embeddings.batches) >= 2
    assert [c.chunk_index for c in service.repo.chunks] == list(range(document.chunk_count))
    assert service.db.commits == 1                                             # chunks + status commit together


def test_a_transient_embedding_outage_propagates_for_retry_and_writes_nothing():
    service = _service(_Embeddings(error=ProviderUnavailableError("openai down")))
    document = _run(service.repo.create_document(NS(title="t", source="s", status=PROCESSING, chunk_count=0, error=None, content="hello world")))
    with pytest.raises(ProviderUnavailableError):
        _run(service.process(document.id))
    assert document.status == PROCESSING and service.repo.chunks == [] and service.db.rollbacks == 1


def test_a_permanent_failure_marks_the_document_failed_with_a_short_reason():
    service = _service(_Embeddings(error=ValueError("bad input")))
    document = _run(service.repo.create_document(NS(title="t", source="s", status=PROCESSING, chunk_count=0, error=None, content="hello world")))
    assert _run(service.process(document.id)) == FAILED
    assert document.status == FAILED and document.error == "Embedding failed." and service.repo.chunks == []


def test_duplicate_delivery_and_unknown_ids_are_harmless():
    service = _service()
    document = _run(service.repo.create_document(NS(title="t", source="s", status=READY, chunk_count=2, error=None, content="x")))
    assert _run(service.process(document.id)) == "skipped" and service.embeddings.batches == []
    assert _run(service.process(uuid.uuid4())) == "missing"


def test_giving_up_marks_only_documents_still_processing():
    service = _service()
    pending = _run(service.repo.create_document(NS(title="t", source="s", status=PROCESSING, chunk_count=0, error=None, content="x")))
    done = _run(service.repo.create_document(NS(title="t", source="s", status=READY, chunk_count=1, error=None, content="x")))
    _run(service.mark_failed(pending.id, "Embedding provider unavailable after retries."))
    _run(service.mark_failed(done.id, "nope"))
    assert pending.status == FAILED and pending.error.startswith("Embedding provider") and done.status == READY
