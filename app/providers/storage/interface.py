"""
Storage provider abstraction (Master Blueprint principle: provider
abstraction — never couple business logic to one vendor).

Two concrete providers implement this interface:
  - CloudinaryProvider        -> optimized/CDN images (avatars, destinations)
  - SupabaseStorageProvider   -> arbitrary files with RLS (attachments, PDFs, docs)

The routing between them is decided centrally in
app/providers/storage/factory.py based on UploadUseCase, so callers
never need to know which vendor is behind a given upload.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Optional


@dataclass(frozen=True)
class UploadResult:
    url: str
    storage_key: str          # provider-specific identifier (public_id / storage path)
    provider: str              # "cloudinary" | "supabase_storage"
    bytes_size: int
    content_type: str
    is_signed_url: bool = False


class StorageProvider(ABC):
    @abstractmethod
    async def upload(
        self,
        *,
        file_bytes: bytes,
        filename: str,
        content_type: str,
        folder: str,
        user_id: Optional[str] = None,
    ) -> UploadResult:
        """Upload a file and return its public/signed URL and storage key."""
        raise NotImplementedError

    @abstractmethod
    async def delete(self, storage_key: str) -> bool:
        """Delete a previously uploaded file. Returns True on success."""
        raise NotImplementedError

    async def download(self, storage_key: str) -> bytes:
        """Fetch a stored file's bytes (used by background workers). Not every provider
        supports it (Cloudinary is an image CDN); only private-file storage needs it."""
        raise NotImplementedError(f"{type(self).__name__} does not support downloads.")

    @abstractmethod
    async def get_signed_url(self, storage_key: str, expires_in_seconds: int = 3600) -> str:
        """Return a time-limited signed URL for private files."""
        raise NotImplementedError
