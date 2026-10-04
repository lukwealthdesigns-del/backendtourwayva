"""
Upload endpoints. Provider routing (Cloudinary vs Supabase Storage)
is fully transparent to the client — it just POSTs a file to the
endpoint matching what it's uploading.

File validation (size, real content type, malware scan, filename
sanitization) lives in UploadService and is server-side only.

`/uploads/attachment` and `/uploads/travel-document` are kept for API
compatibility but are thin wrappers over AttachmentService — the same code
path as `POST /attachments/upload` — so every file they accept now gets a
tracked `attachments` row (they used to store an untracked, unlisted file).
Prefer `POST /attachments/upload?category=...` in new code: it returns the
full attachment record (id, extraction status, ...) rather than just a URL.
"""
from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, File, UploadFile
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies import get_current_user, rate_limit, require_feature
from app.core.constants import AttachmentCategory, FeatureFlag
from app.db.models.user import User
from app.db.session import get_db
from app.modules.attachments.service import AttachmentService
from app.modules.trips.service import TripService
from app.modules.uploads.schemas import UploadResponse
from app.modules.uploads.service import UploadService
from app.repositories.user_repository import UserRepository

router = APIRouter(
    prefix="/uploads",
    tags=["Uploads"],
    dependencies=[Depends(rate_limit(bucket="uploads", max_requests=20, window_seconds=300, per="user"))],
)


def get_upload_service() -> UploadService:
    return UploadService()


def _to_response(result) -> UploadResponse:
    return UploadResponse(
        url=result.url,
        storage_key=result.storage_key,
        provider=result.provider,
        bytes_size=result.bytes_size,
        content_type=result.content_type,
        is_signed_url=result.is_signed_url,
    )


@router.post("/avatar", response_model=UploadResponse)
async def upload_avatar(
    file: UploadFile = File(...),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
    service: UploadService = Depends(get_upload_service),
):
    """Uploads to Cloudinary and SAVES the result as the user's avatar.
    Re-uploading replaces the previous avatar (deterministic public_id
    keyed on user_id)."""
    result = await service.upload_avatar(file=file, user_id=current_user.id)
    current_user.avatar_url = result.url
    await UserRepository(db).save(current_user)
    await db.commit()
    return _to_response(result)


@router.post(
    "/attachment", response_model=UploadResponse, deprecated=True,
    dependencies=[Depends(require_feature(FeatureFlag.ATTACHMENTS))],
)
async def upload_attachment(
    file: UploadFile = File(...),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Deprecated wrapper: same tracked path as `POST /attachments/upload`
    (a database row is created and the file is text-extracted), returning
    only the storage details. Use `POST /attachments/upload` instead."""
    _, result = await AttachmentService(db).upload_tracked(
        file=file, user_id=current_user.id, category=AttachmentCategory.ATTACHMENT
    )
    return _to_response(result)


@router.post("/trip-pdf/{trip_id}", response_model=UploadResponse, dependencies=[Depends(require_feature(FeatureFlag.PDF_EXPORT))])
async def upload_trip_pdf(
    trip_id: uuid.UUID,
    file: UploadFile = File(...),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Uploads to Supabase Storage and records a tracked row. The caller must be
    an owner/editor of the trip — verified server-side before anything is stored."""
    await TripService(db).get_trip_authorized(trip_id=trip_id, user_id=current_user.id, require_editor=True)
    _, result = await AttachmentService(db).upload_trip_pdf_tracked(
        file=file, user_id=current_user.id, trip_id=trip_id
    )
    return _to_response(result)


@router.post(
    "/travel-document", response_model=UploadResponse, deprecated=True,
    dependencies=[Depends(require_feature(FeatureFlag.ATTACHMENTS))],
)
async def upload_travel_document(
    file: UploadFile = File(...),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Deprecated wrapper: same tracked path as
    `POST /attachments/upload?category=travel_document` (private bucket, a
    database row is created, NOT text-extracted). Use that instead."""
    _, result = await AttachmentService(db).upload_tracked(
        file=file, user_id=current_user.id, category=AttachmentCategory.TRAVEL_DOCUMENT
    )
    return _to_response(result)
