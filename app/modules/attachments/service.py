"""
AttachmentService (Master Blueprint §42) — full pipeline: uses the
existing UploadService (Phase 1, Supabase Storage) for storage, then
runs extraction (LocalTextExtractionProvider) and structured-field
heuristics, and exposes confirm/link-to-trip as separate steps so the
person reviews what was extracted before it's treated as trusted data.
"""
from __future__ import annotations

import asyncio
import uuid
from typing import Optional

from fastapi import UploadFile
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.constants import AttachmentCategory, AttachmentExtractionStatus, UploadUseCase
from app.core.exceptions import ForbiddenError, NotFoundError, ValidationAppError
from app.core.logging import get_logger
from app.db.models.attachment import Attachment
from app.modules.attachments.processing import process_attachment
from app.modules.trips.service import TripService
from app.providers.storage.interface import UploadResult
from app.modules.uploads.service import UploadService
from app.providers.storage.factory import get_storage_provider
from app.utils.files import display_filename
from app.repositories.attachment_repository import AttachmentRepository

logger = get_logger(__name__)

def use_case_for(category: AttachmentCategory) -> UploadUseCase:
    """AttachmentCategory and UploadUseCase share their values on purpose:
    the category stored on a row is exactly what routes it to its bucket."""
    return UploadUseCase(category.value)


class AttachmentService:
    # Categories a CLIENT may upload. TRIP_PDF rows are generated server-side
    # (TripPDFService) and can never be uploaded through these paths.
    UPLOADABLE_CATEGORIES = frozenset({AttachmentCategory.ATTACHMENT, AttachmentCategory.TRAVEL_DOCUMENT})

    def __init__(self, db: AsyncSession):
        self.db = db
        self.repo = AttachmentRepository(db)
        self.trip_service = TripService(db)
        self.upload_service = UploadService()

    async def upload_and_process(
        self,
        *,
        file: UploadFile,
        user_id: uuid.UUID,
        trip_id: Optional[uuid.UUID] = None,
        category: AttachmentCategory = AttachmentCategory.ATTACHMENT,
    ) -> Attachment:
        attachment, _ = await self.upload_tracked(file=file, user_id=user_id, trip_id=trip_id, category=category)
        return attachment

    async def upload_tracked(
        self,
        *,
        file: UploadFile,
        user_id: uuid.UUID,
        trip_id: Optional[uuid.UUID] = None,
        category: AttachmentCategory = AttachmentCategory.ATTACHMENT,
    ) -> tuple[Attachment, UploadResult]:
        """The ONE upload path for user files. Validates, stores in the
        category's bucket, and ALWAYS writes an `attachments` row — the raw
        /uploads/attachment and /uploads/travel-document endpoints delegate
        here too, so no upload can exist without a database row (it used to be
        possible, leaving unlisted files only purged on account deletion).
        Returns the row and the storage result (which carries the byte size the
        row itself does not store)."""
        if category not in self.UPLOADABLE_CATEGORIES:
            raise ValidationAppError("This category cannot be uploaded directly.")
        if trip_id is not None:
            # Verify membership up front — never link to a trip the
            # uploader can't access.
            await self.trip_service.get_trip_authorized(trip_id=trip_id, user_id=user_id)

        # Validate ONCE (size, real content type from magic bytes, malware
        # scan, filename sanitization) and reuse the same bytes for both
        # storage and text extraction.
        is_travel_document = category == AttachmentCategory.TRAVEL_DOCUMENT
        validated = await self.upload_service.read_validated(
            file,
            max_mb=settings.MAX_ATTACHMENT_SIZE_MB,
            allowed_mimes=set(settings.ALLOWED_ATTACHMENT_MIME_TYPES),
            default_name="document" if is_travel_document else "attachment",
        )
        content = validated.content
        upload_result = await self.upload_service.store_validated_attachment(
            validated, user_id=user_id, use_case=use_case_for(category)
        )

        attachment = Attachment(
            user_id=user_id, trip_id=trip_id, category=category,
            original_filename=display_filename(file.filename, default="document" if is_travel_document else "attachment"),
            content_type=upload_result.content_type,
            storage_key=upload_result.storage_key, url=upload_result.url,
            # Travel documents (passports, visas, ID cards) are deliberately NOT
            # run through OCR / text extraction: their text would be copied into
            # the database and surfaced by the structured-field heuristics. They
            # are stored privately and returned as-is.
            extraction_status=(
                AttachmentExtractionStatus.UNSUPPORTED if is_travel_document else AttachmentExtractionStatus.PENDING
            ),
        )
        await self.repo.create(attachment)
        await self.db.commit()

        if not is_travel_document:
            # Extraction/OCR is CPU-heavy: in production it runs in a worker and the response returns
            # at once with extraction_status="pending" (clients poll GET /attachments/{id}).
            await self._process(attachment.id, content)
        return attachment, upload_result

    async def upload_trip_pdf_tracked(
        self, *, file: UploadFile, user_id: uuid.UUID, trip_id: uuid.UUID
    ) -> tuple[Attachment, UploadResult]:
        """A user-supplied PDF for a trip (e.g. an itinerary they made elsewhere).
        Same rule as every other upload: it gets a row. Editor access to the
        trip is verified here, not only in the router."""
        await self.trip_service.get_trip_authorized(trip_id=trip_id, user_id=user_id, require_editor=True)
        upload_result = await self.upload_service.upload_trip_pdf(file=file, user_id=user_id, trip_id=trip_id)
        attachment = Attachment(
            user_id=user_id, trip_id=trip_id, category=AttachmentCategory.TRIP_PDF,
            original_filename=display_filename(file.filename, default="trip.pdf"),
            content_type=upload_result.content_type,
            storage_key=upload_result.storage_key, url=upload_result.url,
            extraction_status=AttachmentExtractionStatus.UNSUPPORTED,   # nothing is extracted from it
        )
        await self.repo.create(attachment)
        await self.db.commit()
        return attachment, upload_result

    async def _process(self, attachment_id: uuid.UUID, content: bytes) -> None:
        if settings.tasks_execution == "background":
            try:
                from app.workers.celery_app import celery_app

                await asyncio.to_thread(celery_app.send_task, "attachments.process", args=[str(attachment_id)])
                return
            except Exception as exc:  # noqa: BLE001
                # Broker down: we still hold the bytes, so process here rather than leave it pending.
                logger.error("attachment_enqueue_failed", error=str(exc))
        await process_attachment(self.db, attachment_id, content=content)

    @staticmethod
    async def fresh_url(attachment: Attachment) -> str:
        """The URL stored on the row is a signed URL that expired an hour
        after upload, so every response mints a new short-lived one from the
        storage key instead."""
        # Route by the row's own category: a trip PDF or travel document lives in a
        # different bucket than a general attachment, and signing against the wrong
        # bucket yields a URL that does not work.
        provider = get_storage_provider(use_case_for(attachment.category))
        try:
            return await provider.get_signed_url(attachment.storage_key)
        except Exception as exc:  # noqa: BLE001
            logger.warning("attachment_fresh_url_failed", attachment_id=str(attachment.id), error=str(exc))
            return ""

    async def get_owned(self, *, attachment_id: uuid.UUID, user_id: uuid.UUID) -> Attachment:
        attachment = await self.repo.get(attachment_id)
        if attachment is None:
            raise NotFoundError("Attachment not found.")
        if attachment.user_id != user_id:
            raise ForbiddenError("You do not have access to this attachment.")
        return attachment

    async def list_my_attachments(self, user_id: uuid.UUID, *, category: Optional[AttachmentCategory] = None):
        return await self.repo.list_for_user(user_id, category=category)

    async def confirm(self, *, attachment_id: uuid.UUID, user_id: uuid.UUID) -> Attachment:
        attachment = await self.get_owned(attachment_id=attachment_id, user_id=user_id)
        attachment.user_confirmed = True
        await self.repo.save(attachment)
        await self.db.commit()
        return attachment

    async def link_to_trip(self, *, attachment_id: uuid.UUID, user_id: uuid.UUID, trip_id: uuid.UUID) -> Attachment:
        attachment = await self.get_owned(attachment_id=attachment_id, user_id=user_id)
        await self.trip_service.get_trip_authorized(trip_id=trip_id, user_id=user_id)
        attachment.trip_id = trip_id
        await self.repo.save(attachment)
        await self.db.commit()
        return attachment
