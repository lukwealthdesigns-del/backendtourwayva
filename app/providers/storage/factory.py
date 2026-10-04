"""
Storage routing table.

This is the single place that decides which provider handles which
kind of upload, per the requested adjustment:

    User Avatars           -> Cloudinary (optimization + CDN)
    Destination Images     -> Cloudinary (optimization + CDN)
    Attachments (PDF/Docs) -> Supabase Storage (RLS + any file type)
    Trip PDFs              -> Supabase Storage (any file type)
    Travel Documents       -> Supabase Storage (any file type)

Callers (services/routers) never instantiate a provider directly —
they call `get_storage_provider(use_case)` so the routing stays
centralized and swappable.
"""
from __future__ import annotations

from app.core.config import settings
from app.core.constants import UploadUseCase
from app.providers.storage.cloudinary_provider import CloudinaryProvider
from app.providers.storage.interface import StorageProvider
from app.providers.storage.supabase_storage_provider import SupabaseStorageProvider

_cloudinary_provider = CloudinaryProvider()


def get_storage_provider(use_case: UploadUseCase) -> StorageProvider:
    if use_case == UploadUseCase.USER_AVATAR:
        return _cloudinary_provider

    if use_case == UploadUseCase.DESTINATION_IMAGE:
        return _cloudinary_provider

    if use_case == UploadUseCase.ATTACHMENT:
        return SupabaseStorageProvider(bucket=settings.SUPABASE_STORAGE_ATTACHMENTS_BUCKET)

    if use_case == UploadUseCase.TRIP_PDF:
        return SupabaseStorageProvider(bucket=settings.SUPABASE_STORAGE_TRIP_PDFS_BUCKET)

    if use_case == UploadUseCase.TRAVEL_DOCUMENT:
        return SupabaseStorageProvider(bucket=settings.SUPABASE_STORAGE_TRAVEL_DOCS_BUCKET)

    raise ValueError(f"No storage provider routed for use case: {use_case}")


def get_cloudinary_folder(use_case: UploadUseCase) -> str:
    if use_case == UploadUseCase.USER_AVATAR:
        return settings.CLOUDINARY_AVATAR_FOLDER
    if use_case == UploadUseCase.DESTINATION_IMAGE:
        return settings.CLOUDINARY_DESTINATION_FOLDER
    raise ValueError(f"{use_case} is not a Cloudinary use case.")
