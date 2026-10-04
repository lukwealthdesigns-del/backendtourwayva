"""Every upload path writes an `attachments` row and is routed to its own bucket.

Before this, /uploads/attachment, /uploads/travel-document and
/uploads/trip-pdf stored files that had no database row, and every row was
signed against the attachments bucket regardless of where the file lived.
No network, no database: fakes stand in for storage, the repository and trips.
"""
from __future__ import annotations

import asyncio
import uuid
from types import SimpleNamespace as NS

import pytest

from app.core.constants import AttachmentCategory as Cat
from app.core.constants import AttachmentExtractionStatus as Status
from app.core.constants import UploadUseCase
from app.core.exceptions import ForbiddenError, ValidationAppError
from app.modules.attachments import service as service_module
from app.modules.attachments.service import AttachmentService, use_case_for
from app.providers.storage.interface import UploadResult


def _run(coro):
    return asyncio.run(coro)


class _FakeDB:
    def __init__(self):
        self.commits = 0

    async def commit(self):
        self.commits += 1


class _Repo:
    def __init__(self):
        self.rows = []

    async def create(self, attachment):
        attachment.id = attachment.id or uuid.uuid4()
        self.rows.append(attachment)
        return attachment


class _Trips:
    def __init__(self, *, allowed=True):
        self.allowed = allowed
        self.calls = []

    async def get_trip_authorized(self, *, trip_id, user_id, require_editor=False):
        self.calls.append({"trip_id": trip_id, "require_editor": require_editor})
        if not self.allowed:
            raise ForbiddenError("no access")
        return NS(id=trip_id)


class _Uploads:
    """Stands in for UploadService: records which bucket a file was routed to."""

    def __init__(self):
        self.stored_use_cases = []
        self.validated_calls = []

    async def read_validated(self, file, *, max_mb, allowed_mimes, default_name):
        self.validated_calls.append({"default_name": default_name})
        return NS(content=b"%PDF-1.7 bytes", content_type="application/pdf", filename="file.pdf")

    async def store_validated_attachment(self, validated, *, user_id, use_case):
        self.stored_use_cases.append(use_case)
        return UploadResult(
            url="https://signed.example/x", storage_key=f"users/{user_id}/file.pdf", provider="supabase_storage",
            bytes_size=len(validated.content), content_type=validated.content_type, is_signed_url=True,
        )

    async def upload_trip_pdf(self, *, file, user_id, trip_id):
        self.stored_use_cases.append(UploadUseCase.TRIP_PDF)
        return UploadResult(
            url="https://signed.example/t", storage_key=f"trips/{trip_id}/file.pdf", provider="supabase_storage",
            bytes_size=10, content_type="application/pdf", is_signed_url=True,
        )


def _service(monkeypatch, *, trips=None):
    service = AttachmentService.__new__(AttachmentService)
    service.db, service.repo = _FakeDB(), _Repo()
    service.trip_service = trips or _Trips()
    service.upload_service = _Uploads()
    processed = []

    async def fake_process(attachment_id, content):
        processed.append(attachment_id)

    service._process = fake_process
    service._processed = processed
    return service


_FILE = NS(filename="Booking Confirmation.pdf")


def test_category_and_upload_use_case_share_values_so_a_row_routes_back_to_its_own_bucket():
    assert {c.value for c in Cat} == {u.value for u in (
        UploadUseCase.ATTACHMENT, UploadUseCase.TRAVEL_DOCUMENT, UploadUseCase.TRIP_PDF)}
    assert use_case_for(Cat.ATTACHMENT) == UploadUseCase.ATTACHMENT
    assert use_case_for(Cat.TRAVEL_DOCUMENT) == UploadUseCase.TRAVEL_DOCUMENT
    assert use_case_for(Cat.TRIP_PDF) == UploadUseCase.TRIP_PDF


def test_an_attachment_upload_writes_a_row_in_the_attachments_bucket_and_is_extracted(monkeypatch):
    service, user_id = _service(monkeypatch), uuid.uuid4()

    attachment, result = _run(service.upload_tracked(file=_FILE, user_id=user_id))

    assert service.repo.rows == [attachment] and attachment.category == Cat.ATTACHMENT
    assert service.upload_service.stored_use_cases == [UploadUseCase.ATTACHMENT]
    assert attachment.extraction_status == Status.PENDING and service._processed == [attachment.id]
    assert attachment.storage_key == result.storage_key and attachment.original_filename == "Booking Confirmation.pdf"
    assert result.bytes_size == len(b"%PDF-1.7 bytes")           # the row does not store size; the result carries it


def test_a_travel_document_goes_to_its_own_bucket_and_is_never_text_extracted(monkeypatch):
    service = _service(monkeypatch)

    attachment, _ = _run(service.upload_tracked(
        file=NS(filename="passport.pdf"), user_id=uuid.uuid4(), category=Cat.TRAVEL_DOCUMENT))

    assert attachment.category == Cat.TRAVEL_DOCUMENT
    assert service.upload_service.stored_use_cases == [UploadUseCase.TRAVEL_DOCUMENT]
    # A passport's text must not be copied into the database by OCR/heuristics.
    assert attachment.extraction_status == Status.UNSUPPORTED and service._processed == []
    assert service.upload_service.validated_calls[0]["default_name"] == "document"


def test_a_trip_pdf_cannot_be_uploaded_through_the_generic_path(monkeypatch):
    service = _service(monkeypatch)

    with pytest.raises(ValidationAppError):
        _run(service.upload_tracked(file=_FILE, user_id=uuid.uuid4(), category=Cat.TRIP_PDF))

    assert service.repo.rows == [] and service.upload_service.stored_use_cases == []


def test_linking_to_a_trip_is_membership_checked_before_anything_is_stored(monkeypatch):
    trips = _Trips(allowed=False)
    service, trip_id = _service(monkeypatch, trips=trips), uuid.uuid4()

    with pytest.raises(ForbiddenError):
        _run(service.upload_tracked(file=_FILE, user_id=uuid.uuid4(), trip_id=trip_id))

    assert service.upload_service.validated_calls == [] and service.repo.rows == []


def test_the_raw_trip_pdf_upload_is_tracked_too_and_requires_editor_access(monkeypatch):
    trips = _Trips()
    service, user_id, trip_id = _service(monkeypatch, trips=trips), uuid.uuid4(), uuid.uuid4()

    attachment, result = _run(service.upload_trip_pdf_tracked(file=NS(filename="plan.pdf"), user_id=user_id, trip_id=trip_id))

    assert trips.calls == [{"trip_id": trip_id, "require_editor": True}]
    assert attachment.category == Cat.TRIP_PDF and attachment.trip_id == trip_id
    assert attachment.storage_key == f"trips/{trip_id}/file.pdf" and service.repo.rows == [attachment]
    assert attachment.extraction_status == Status.UNSUPPORTED and service._processed == []


def test_a_viewer_cannot_upload_a_trip_pdf_and_nothing_is_stored(monkeypatch):
    service = _service(monkeypatch, trips=_Trips(allowed=False))

    with pytest.raises(ForbiddenError):
        _run(service.upload_trip_pdf_tracked(file=NS(filename="plan.pdf"), user_id=uuid.uuid4(), trip_id=uuid.uuid4()))

    assert service.upload_service.stored_use_cases == [] and service.repo.rows == []


@pytest.mark.parametrize("category", list(Cat))
def test_a_fresh_url_is_signed_against_the_rows_own_bucket(monkeypatch, category):
    asked = []

    class _Provider:
        async def get_signed_url(self, key):
            return f"https://signed/{key}"

    monkeypatch.setattr(service_module, "get_storage_provider", lambda use_case: asked.append(use_case) or _Provider())

    url = _run(AttachmentService.fresh_url(NS(id=uuid.uuid4(), category=category, storage_key="k")))

    assert url == "https://signed/k" and asked == [UploadUseCase(category.value)]


def test_a_signing_failure_yields_an_empty_url_instead_of_an_error(monkeypatch):
    class _Provider:
        async def get_signed_url(self, key):
            raise RuntimeError("storage down")

    monkeypatch.setattr(service_module, "get_storage_provider", lambda use_case: _Provider())

    assert _run(AttachmentService.fresh_url(NS(id=uuid.uuid4(), category=Cat.ATTACHMENT, storage_key="k"))) == ""
