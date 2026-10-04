"""
Supabase Storage provider — used for Attachments (PDFs/Docs), Trip
PDFs, and Travel Documents.

Chosen for these use cases (vs. Cloudinary) because:
  - They are arbitrary file types (PDF, DOCX, etc.), not just images.
  - Access must be governed by Supabase Row-Level Security policies
    tied to the same auth/ownership model as the rest of the database
    (trip membership, user ownership) — not just "anyone with the URL".

Each use case is stored in its own private bucket (see
SUPABASE_STORAGE_*_BUCKET settings) and served via short-lived signed
URLs rather than public links.
"""
from __future__ import annotations

import asyncio
from typing import Optional

from supabase import Client, create_client

from app.core.config import settings
from app.core.exceptions import ProviderUnavailableError
from app.core.logging import get_logger
from app.providers.storage.interface import StorageProvider, UploadResult

logger = get_logger(__name__)

_client: Optional[Client] = None


def _get_client() -> Client:
    global _client
    if _client is not None:
        return _client
    if not (settings.SUPABASE_URL and settings.SUPABASE_SERVICE_ROLE_KEY):
        raise ProviderUnavailableError("Supabase Storage is not configured.")
    # Service-role key is backend-only — NEVER expose it to the frontend.
    _client = create_client(settings.SUPABASE_URL, settings.SUPABASE_SERVICE_ROLE_KEY)
    return _client


class SupabaseStorageProvider(StorageProvider):
    def __init__(self, bucket: str):
        self.bucket = bucket

    async def upload(
        self,
        *,
        file_bytes: bytes,
        filename: str,
        content_type: str,
        folder: str,
        user_id: Optional[str] = None,
    ) -> UploadResult:
        client = _get_client()
        storage_key = f"{folder}/{filename}"

        try:
            # The supabase client is synchronous: run it in a worker thread so a
            # slow upload never blocks the event loop. upsert is OFF — callers
            # pass unique keys, and an existing object must never be overwritten.
            await asyncio.to_thread(
                client.storage.from_(self.bucket).upload,
                path=storage_key,
                file=file_bytes,
                file_options={"content-type": content_type, "upsert": "false"},
            )
        except Exception as exc:  # noqa: BLE001
            logger.error(
                "supabase_storage_upload_failed",
                error=str(exc),
                bucket=self.bucket,
                storage_key=storage_key,
            )
            raise ProviderUnavailableError("File upload failed. Please try again.") from exc

        signed_url = await self.get_signed_url(storage_key)

        return UploadResult(
            url=signed_url,
            storage_key=storage_key,
            provider="supabase_storage",
            bytes_size=len(file_bytes),
            content_type=content_type,
            is_signed_url=True,
        )

    async def delete(self, storage_key: str) -> bool:
        client = _get_client()
        try:
            await asyncio.to_thread(client.storage.from_(self.bucket).remove, [storage_key])
            return True
        except Exception as exc:  # noqa: BLE001
            logger.error("supabase_storage_delete_failed", error=str(exc), storage_key=storage_key)
            return False

    async def download(self, storage_key: str) -> bytes:
        client = _get_client()
        try:
            return await asyncio.to_thread(client.storage.from_(self.bucket).download, storage_key)
        except Exception as exc:  # noqa: BLE001
            logger.error("supabase_download_failed", error=str(exc), storage_key=storage_key)
            raise ProviderUnavailableError("Could not read the stored file.") from exc

    async def get_signed_url(self, storage_key: str, expires_in_seconds: int = 3600) -> str:
        client = _get_client()
        try:
            result = await asyncio.to_thread(
                client.storage.from_(self.bucket).create_signed_url, storage_key, expires_in_seconds
            )
            return result["signedURL"] if "signedURL" in result else result.get("signed_url", "")
        except Exception as exc:  # noqa: BLE001
            logger.error("supabase_signed_url_failed", error=str(exc), storage_key=storage_key)
            raise ProviderUnavailableError("Could not generate a secure file link.") from exc

    async def delete_prefix(self, prefix: str, *, page_size: int = 100, max_pages: int = 50) -> int:
        """Remove every object directly under `prefix` (e.g. `users/<id>`).
        Used by account deletion to purge files that have no database row.
        Returns how many objects were removed."""
        client = _get_client()
        removed = 0
        for _ in range(max_pages):
            entries = await asyncio.to_thread(
                client.storage.from_(self.bucket).list, prefix, {"limit": page_size}
            )
            names = [e["name"] for e in (entries or []) if e.get("name")]
            if not names:
                break
            await asyncio.to_thread(
                client.storage.from_(self.bucket).remove, [f"{prefix}/{name}" for name in names]
            )
            removed += len(names)
            if len(names) < page_size:
                break
        return removed
