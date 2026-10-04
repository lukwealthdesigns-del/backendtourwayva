"""
Attachment text extraction / OCR / structured-field parsing (Master Prompt §42, §69).

Runs in a Celery worker in production (`attachments.process`) so a slow OCR pass never
ties up an API worker, and inline in development. The upload request stores the file and
returns immediately with `extraction_status = "pending"`; clients poll GET /attachments/{id}.

Idempotent and safe under duplicate delivery: the row is locked, and only a PENDING
attachment is processed.
"""
from __future__ import annotations

import uuid
from typing import Optional

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.constants import AttachmentExtractionStatus, UploadUseCase
from app.core.exceptions import ProviderUnavailableError
from app.core.logging import get_logger
from app.modules.attachments.structured_extraction import extract_structured_fields
from app.providers.extraction.interface import TextExtractionProvider
from app.providers.extraction.local_provider import LocalTextExtractionProvider
from app.providers.storage.factory import get_storage_provider
from app.providers.storage.interface import StorageProvider
from app.repositories.attachment_repository import AttachmentRepository

logger = get_logger(__name__)


async def process_attachment(
    db: AsyncSession,
    attachment_id: uuid.UUID,
    *,
    content: Optional[bytes] = None,
    storage: Optional[StorageProvider] = None,
    extractor: Optional[TextExtractionProvider] = None,
) -> str:
    """Returns "completed" | "unsupported" | "failed" | "skipped" | "missing".

    A TRANSIENT storage failure (ProviderUnavailableError) propagates so the task retries;
    anything else marks the attachment FAILED (retrying a corrupt file achieves nothing)."""
    repo = AttachmentRepository(db)
    attachment = await repo.get_for_update(attachment_id)
    if attachment is None:
        return "missing"
    if attachment.extraction_status != AttachmentExtractionStatus.PENDING:
        return "skipped"

    try:
        if content is None:
            provider = storage or get_storage_provider(UploadUseCase(attachment.category.value))
            content = await provider.download(attachment.storage_key)
        extraction = await (extractor or LocalTextExtractionProvider()).extract(content, attachment.content_type)
        if not extraction.text:
            attachment.extraction_status = AttachmentExtractionStatus.UNSUPPORTED
        else:
            attachment.extracted_text = extraction.text
            attachment.structured_fields = extract_structured_fields(extraction.text)
            attachment.extraction_status = AttachmentExtractionStatus.COMPLETED
    except ProviderUnavailableError:
        await db.rollback()
        raise
    except Exception as exc:  # noqa: BLE001
        logger.warning("attachment_extraction_failed", attachment_id=str(attachment_id), error=str(exc))
        attachment.extraction_status = AttachmentExtractionStatus.FAILED

    await repo.save(attachment)
    await db.commit()
    return attachment.extraction_status.value


async def mark_failed(db: AsyncSession, attachment_id: uuid.UUID) -> None:
    """Called when the task has exhausted its retries."""
    repo = AttachmentRepository(db)
    attachment = await repo.get_for_update(attachment_id)
    if attachment is not None and attachment.extraction_status == AttachmentExtractionStatus.PENDING:
        attachment.extraction_status = AttachmentExtractionStatus.FAILED
        await repo.save(attachment)
        await db.commit()
