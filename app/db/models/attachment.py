"""
Attachment model (Master Blueprint §42).

Pipeline implemented end-to-end: upload -> auth/authorization
(already enforced by the endpoint) -> file/size/MIME validation
(already enforced by UploadService) -> storage (Supabase, Phase 1) ->
extraction/OCR (pypdf/pytesseract, see app/providers/extraction) ->
structured information (regex heuristics, see
app/modules/attachments/structured_extraction.py) -> user
confirmation (`user_confirmed` flag + PATCH endpoint) -> optional
trip integration (`trip_id`, settable at upload or afterward).

Extraction runs synchronously in the request today (fine for typical
attachment sizes); moving it to a Celery background task (the
existing worker container already runs one) is a reasonable Phase 8
hardening step for large files.
"""
from __future__ import annotations

import uuid
from typing import Optional

from sqlalchemy import Boolean, Enum, ForeignKey, String, Text
from sqlalchemy.dialects.postgresql import JSON, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.constants import AttachmentCategory, AttachmentExtractionStatus
from app.db.base import Base, TimestampMixin, UUIDPKMixin, enum_values


class Attachment(UUIDPKMixin, TimestampMixin, Base):
    __tablename__ = "attachments"

    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    trip_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True), ForeignKey("trips.id", ondelete="SET NULL"), nullable=True, index=True
    )
    original_filename: Mapped[str] = mapped_column(String(255), nullable=False)
    content_type: Mapped[str] = mapped_column(String(100), nullable=False)
    storage_key: Mapped[str] = mapped_column(String(500), nullable=False)
    url: Mapped[str] = mapped_column(String(1000), nullable=False)
    # Distinguishes a general Attachment (booking confirmations, PDFs) from a
    # Travel Document — both are Supabase Storage use cases, now both tracked
    # here so every upload has a database row (closes the untracked-upload gap).
    category: Mapped[AttachmentCategory] = mapped_column(
        Enum(AttachmentCategory, name="attachment_category_enum", values_callable=enum_values),
        default=AttachmentCategory.ATTACHMENT, nullable=False,
    )
    extraction_status: Mapped[AttachmentExtractionStatus] = mapped_column(
        Enum(AttachmentExtractionStatus, name="attachment_extraction_status_enum", values_callable=enum_values),
        default=AttachmentExtractionStatus.PENDING, nullable=False,
    )
    extracted_text: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    structured_fields: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)
    user_confirmed: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
