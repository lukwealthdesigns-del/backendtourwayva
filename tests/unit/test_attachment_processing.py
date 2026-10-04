"""Background attachment extraction: idempotent, retry-aware, and never blocks the upload."""
from __future__ import annotations

import asyncio
import uuid
from types import SimpleNamespace as NS

import pytest

from app.core.config import settings
from app.core.constants import AttachmentExtractionStatus as Status
from app.core.exceptions import ProviderUnavailableError
from app.modules.attachments import processing as processing_module
from app.modules.attachments import service as service_module
from app.modules.attachments.processing import mark_failed, process_attachment
from app.modules.attachments.service import AttachmentService


class _FakeDB:
    def __init__(self):
        self.commits = 0
        self.rollbacks = 0

    async def commit(self):
        self.commits += 1

    async def rollback(self):
        self.rollbacks += 1


class _Repo:
    def __init__(self, attachment):
        self.attachment = attachment

    async def get_for_update(self, attachment_id):
        return self.attachment

    async def save(self, attachment):
        return attachment


def _attachment(status=Status.PENDING, content_type="application/pdf"):
    return NS(id=uuid.uuid4(), storage_key="users/u/a.pdf", content_type=content_type, extraction_status=status,
              extracted_text=None, structured_fields=None)


class _Storage:
    def __init__(self, data=b"%PDF-bytes", error=None):
        self.data, self.error, self.downloads = data, error, []

    async def download(self, key):
        self.downloads.append(key)
        if self.error:
            raise self.error
        return self.data


class _Extractor:
    def __init__(self, text="Booking ref ABC123\nFlight AP123 on 2026-10-01", error=None):
        self.text, self.error, self.seen = text, error, []

    async def extract(self, content, content_type):
        self.seen.append((content, content_type))
        if self.error:
            raise self.error
        return NS(text=self.text, provider="fake")


def _process(monkeypatch, attachment, **kwargs):
    monkeypatch.setattr(processing_module, "AttachmentRepository", lambda db: _Repo(attachment))
    db = _FakeDB()
    result = asyncio.run(process_attachment(db, attachment.id if attachment else uuid.uuid4(), **kwargs))
    return result, db


def test_a_pending_attachment_is_downloaded_extracted_and_completed(monkeypatch):
    attachment, storage, extractor = _attachment(), _Storage(), _Extractor()
    result, db = _process(monkeypatch, attachment, storage=storage, extractor=extractor)
    assert result == "completed" and attachment.extraction_status == Status.COMPLETED
    assert storage.downloads == ["users/u/a.pdf"]
    assert extractor.seen == [(b"%PDF-bytes", "application/pdf")]
    assert attachment.extracted_text.startswith("Booking ref") and isinstance(attachment.structured_fields, dict)
    assert db.commits == 1


def test_bytes_already_in_hand_skip_the_download(monkeypatch):
    attachment, storage = _attachment(), _Storage()
    _process(monkeypatch, attachment, content=b"raw", storage=storage, extractor=_Extractor())
    assert storage.downloads == []


def test_nothing_extracted_means_unsupported_not_failed(monkeypatch):
    attachment = _attachment(content_type="image/png")
    result, _ = _process(monkeypatch, attachment, storage=_Storage(), extractor=_Extractor(text=""))
    assert result == "unsupported" and attachment.extraction_status == Status.UNSUPPORTED


def test_a_corrupt_file_is_marked_failed_without_retrying(monkeypatch):
    attachment = _attachment()
    result, db = _process(monkeypatch, attachment, storage=_Storage(), extractor=_Extractor(error=ValueError("corrupt pdf")))
    assert result == "failed" and attachment.extraction_status == Status.FAILED and db.commits == 1


def test_a_transient_storage_outage_propagates_so_the_task_retries_and_the_row_stays_pending(monkeypatch):
    attachment = _attachment()
    monkeypatch.setattr(processing_module, "AttachmentRepository", lambda db: _Repo(attachment))
    db = _FakeDB()
    with pytest.raises(ProviderUnavailableError):
        asyncio.run(process_attachment(db, attachment.id, storage=_Storage(error=ProviderUnavailableError("down")),
                                       extractor=_Extractor()))
    assert attachment.extraction_status == Status.PENDING and db.rollbacks == 1 and db.commits == 0


def test_duplicate_delivery_is_harmless(monkeypatch):
    done = _attachment(status=Status.COMPLETED)
    extractor = _Extractor()
    result, db = _process(monkeypatch, done, storage=_Storage(), extractor=extractor)
    assert result == "skipped" and extractor.seen == [] and db.commits == 0

    monkeypatch.setattr(processing_module, "AttachmentRepository", lambda db: _Repo(None))
    assert asyncio.run(process_attachment(_FakeDB(), uuid.uuid4())) == "missing"


def test_giving_up_marks_only_pending_attachments_failed(monkeypatch):
    pending, done = _attachment(), _attachment(status=Status.COMPLETED)
    for attachment in (pending, done):
        monkeypatch.setattr(processing_module, "AttachmentRepository", lambda db, a=attachment: _Repo(a))
        asyncio.run(mark_failed(_FakeDB(), attachment.id))
    assert pending.extraction_status == Status.FAILED and done.extraction_status == Status.COMPLETED


# ---------------------------------------------------------------------------
# The upload path decides between the queue and inline
# ---------------------------------------------------------------------------
def _upload_service(monkeypatch):
    service = AttachmentService.__new__(AttachmentService)
    service.db = _FakeDB()
    calls = []

    async def fake_process(db, attachment_id, *, content=None, **k):
        calls.append(("inline", attachment_id, content))

    monkeypatch.setattr(service_module, "process_attachment", fake_process)
    return service, calls


def test_in_production_the_upload_is_queued_and_not_processed_in_the_request(monkeypatch):
    monkeypatch.setattr(settings, "TASKS_EXECUTION", "background")
    sent = []
    monkeypatch.setattr("app.workers.celery_app.celery_app.send_task",
                        lambda name, args=None, **k: sent.append((name, args)))
    service, calls = _upload_service(monkeypatch)
    attachment_id = uuid.uuid4()
    asyncio.run(service._process(attachment_id, b"bytes"))
    assert sent == [("attachments.process", [str(attachment_id)])] and calls == []


def test_a_dead_broker_falls_back_to_processing_with_the_bytes_already_in_memory(monkeypatch):
    monkeypatch.setattr(settings, "TASKS_EXECUTION", "background")

    def boom(*a, **k):
        raise ConnectionError("broker down")

    monkeypatch.setattr("app.workers.celery_app.celery_app.send_task", boom)
    service, calls = _upload_service(monkeypatch)
    attachment_id = uuid.uuid4()
    asyncio.run(service._process(attachment_id, b"bytes"))
    assert calls == [("inline", attachment_id, b"bytes")]


def test_in_development_the_upload_is_processed_inline(monkeypatch):
    monkeypatch.setattr(settings, "TASKS_EXECUTION", "inline")
    service, calls = _upload_service(monkeypatch)
    attachment_id = uuid.uuid4()
    asyncio.run(service._process(attachment_id, b"bytes"))
    assert calls == [("inline", attachment_id, b"bytes")]
