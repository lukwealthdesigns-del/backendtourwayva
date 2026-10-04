"""
UploadService — validates incoming files, then delegates to the correct
provider via the storage factory. The service layer never knows or cares
whether that provider is Cloudinary or Supabase Storage — see
app/providers/storage/factory.py.

Validation is done on the SERVER from the bytes themselves:

  1. the body is read in bounded chunks and rejected as soon as it exceeds
     the size limit (no unbounded read into memory);
  2. the real content type is detected from magic bytes — the
     client-supplied Content-Type header is ignored (it is trivially
     spoofable);
  3. the detected type must be in the allowed set for the use case;
  4. the bytes are scanned for malware (ClamAV) when configured — with
     REQUIRE_MALWARE_SCAN=true the upload is refused if no scanner is
     available (fail closed);
  5. the filename is sanitized (no path components, safe charset) and the
     storage key gets a random prefix, so a client can never choose where
     a file lands or overwrite an existing object.
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Optional

from fastapi import UploadFile

from app.core.config import settings
from app.core.constants import UploadUseCase
from app.core.exceptions import ProviderUnavailableError, ValidationAppError
from app.core.logging import get_logger
from app.providers.malware.factory import get_malware_scanner
from app.providers.malware.interface import MalwareScanner, ScanStatus
from app.providers.storage.factory import get_cloudinary_folder, get_storage_provider
from app.providers.storage.interface import UploadResult
from app.utils.files import is_legacy_ole_office_mime, sanitize_filename, sniff_content_type

logger = get_logger(__name__)

_IMAGE_MIME_TYPES = {"image/png", "image/jpeg", "image/webp", "image/gif"}
_READ_CHUNK_BYTES = 1024 * 1024


@dataclass(frozen=True)
class ValidatedFile:
    content: bytes
    content_type: str   # detected from the bytes, never from the client header
    filename: str       # sanitized


def _unique_name(safe_filename: str) -> str:
    return f"{uuid.uuid4().hex}_{safe_filename}"


class UploadService:
    def __init__(self, scanner: Optional[MalwareScanner] = None):
        self.scanner = scanner if scanner is not None else get_malware_scanner()

    # --- Validation ---------------------------------------------------
    async def read_validated(
        self, file: UploadFile, *, max_mb: int, allowed_mimes: set[str], default_name: str = "file"
    ) -> ValidatedFile:
        content = await self._read_limited(file, max_bytes=max_mb * 1024 * 1024, max_mb=max_mb)
        if not content:
            raise ValidationAppError("The uploaded file is empty.")

        detected = sniff_content_type(content)
        if detected is None or detected not in allowed_mimes:
            raise ValidationAppError(
                "Unsupported file type.",
                details={"allowed_types": sorted(allowed_mimes)},
            )

        await self._scan(content, content_type=detected)
        return ValidatedFile(
            content=content,
            content_type=detected,
            filename=sanitize_filename(file.filename, default=default_name),
        )

    @staticmethod
    async def _read_limited(file: UploadFile, *, max_bytes: int, max_mb: int) -> bytes:
        chunks: list[bytes] = []
        total = 0
        while True:
            chunk = await file.read(_READ_CHUNK_BYTES)
            if not chunk:
                break
            total += len(chunk)
            if total > max_bytes:
                raise ValidationAppError(f"File exceeds the {max_mb}MB size limit.")
            chunks.append(chunk)
        return b"".join(chunks)

    async def _scan(self, content: bytes, *, content_type: Optional[str] = None) -> None:
        # Legacy OLE Office containers (.doc/.xls/.ppt) can carry macros or
        # embedded OLE objects and are one of the most common real-world
        # malware delivery formats — for THIS format family specifically,
        # a working scanner is mandatory regardless of the global
        # REQUIRE_MALWARE_SCAN setting (fail closed). Every other type
        # (PDF, images, .docx once past the .docm/vbaProject.bin check)
        # keeps following the configured global policy.
        must_scan = settings.REQUIRE_MALWARE_SCAN or is_legacy_ole_office_mime(content_type)

        if self.scanner is None:
            if must_scan:
                raise ProviderUnavailableError("File scanning is required but not configured.")
            return
        try:
            result = await self.scanner.scan(content)
        except ProviderUnavailableError:
            if must_scan:
                raise
            logger.warning("malware_scan_skipped_scanner_unavailable")
            return
        if result.status == ScanStatus.INFECTED:
            logger.warning("malware_detected_upload_rejected", signature=result.signature)
            raise ValidationAppError("The file was rejected by the malware scanner.")

    # --- Use cases ----------------------------------------------------
    async def upload_avatar(self, *, file: UploadFile, user_id: uuid.UUID) -> UploadResult:
        validated = await self.read_validated(
            file, max_mb=settings.MAX_AVATAR_SIZE_MB, allowed_mimes=_IMAGE_MIME_TYPES, default_name="avatar"
        )
        provider = get_storage_provider(UploadUseCase.USER_AVATAR)
        folder = get_cloudinary_folder(UploadUseCase.USER_AVATAR)
        return await provider.upload(
            file_bytes=validated.content,
            filename=validated.filename,
            content_type=validated.content_type,
            folder=folder,
            user_id=str(user_id),
        )

    async def upload_destination_image(self, *, file: UploadFile, destination_key: str) -> UploadResult:
        validated = await self.read_validated(
            file, max_mb=settings.MAX_AVATAR_SIZE_MB, allowed_mimes=_IMAGE_MIME_TYPES, default_name="destination"
        )
        provider = get_storage_provider(UploadUseCase.DESTINATION_IMAGE)
        folder = get_cloudinary_folder(UploadUseCase.DESTINATION_IMAGE)
        return await provider.upload(
            file_bytes=validated.content,
            filename=sanitize_filename(destination_key, default="destination"),
            content_type=validated.content_type,
            folder=folder,
        )

    async def store_validated_attachment(
        self, validated: ValidatedFile, *, user_id: uuid.UUID, use_case: UploadUseCase = UploadUseCase.ATTACHMENT
    ) -> UploadResult:
        """Store a file that already passed `read_validated` — lets callers
        (AttachmentService) validate ONCE and reuse the bytes for text
        extraction instead of reading the upload twice."""
        provider = get_storage_provider(use_case)
        return await provider.upload(
            file_bytes=validated.content,
            filename=_unique_name(validated.filename),
            content_type=validated.content_type,
            folder=f"users/{user_id}",
            user_id=str(user_id),
        )

    async def upload_trip_pdf(self, *, file: UploadFile, user_id: uuid.UUID, trip_id: uuid.UUID) -> UploadResult:
        """Caller MUST have verified that `user_id` may edit `trip_id`."""
        validated = await self.read_validated(
            file,
            max_mb=settings.MAX_ATTACHMENT_SIZE_MB,
            allowed_mimes={"application/pdf"},
            default_name=f"{trip_id}.pdf",
        )
        provider = get_storage_provider(UploadUseCase.TRIP_PDF)
        return await provider.upload(
            file_bytes=validated.content,
            filename=_unique_name(validated.filename),
            content_type="application/pdf",
            folder=f"trips/{trip_id}",
            user_id=str(user_id),
        )
