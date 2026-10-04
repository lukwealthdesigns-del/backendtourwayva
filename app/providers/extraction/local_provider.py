"""
Local text-extraction provider — PDF text extraction via `pypdf`,
image OCR via `pytesseract` (Tesseract OCR, run locally, no API key).

Chosen over a cloud OCR API because no such provider was named
anywhere in the Master Build Prompt (unlike Amadeus/Brevo/Cloudinary),
and this keeps attachment processing usable out of the box without
another account/credential to configure. A cloud provider (AWS
Textract, Google Vision, etc.) can be added later as an alternative
TextExtractionProvider implementation behind the same interface —
nothing else in the codebase would need to change.

Both extractions run in a thread via `asyncio.to_thread` since pypdf
and pytesseract are synchronous, CPU-bound libraries.
"""
from __future__ import annotations

import asyncio
import io

from app.core.logging import get_logger
from app.providers.extraction.interface import ExtractionResult, TextExtractionProvider

logger = get_logger(__name__)

_PDF_MIME = "application/pdf"
_IMAGE_MIMES = {"image/png", "image/jpeg", "image/webp", "image/tiff", "image/bmp"}


def _extract_pdf_text_sync(file_bytes: bytes) -> str:
    from pypdf import PdfReader

    reader = PdfReader(io.BytesIO(file_bytes))
    pages_text = []
    for page in reader.pages:
        try:
            pages_text.append(page.extract_text() or "")
        except Exception as exc:  # noqa: BLE001
            logger.warning("pdf_page_extract_failed", error=str(exc))
    return "\n\n".join(t for t in pages_text if t.strip())


def _extract_image_text_sync(file_bytes: bytes) -> str:
    import pytesseract
    from PIL import Image

    image = Image.open(io.BytesIO(file_bytes))
    try:
        return pytesseract.image_to_string(image) or ""
    except pytesseract.TesseractNotFoundError:
        logger.warning("tesseract_binary_not_found")
        return ""


class LocalTextExtractionProvider(TextExtractionProvider):
    async def extract(self, file_bytes: bytes, content_type: str) -> ExtractionResult:
        if content_type == _PDF_MIME:
            text = await asyncio.to_thread(_extract_pdf_text_sync, file_bytes)
            return ExtractionResult(text=text.strip(), provider="pypdf")

        if content_type in _IMAGE_MIMES:
            text = await asyncio.to_thread(_extract_image_text_sync, file_bytes)
            return ExtractionResult(text=text.strip(), provider="tesseract")

        # Unsupported format — never fabricate extracted content.
        return ExtractionResult(text="", provider="none")
