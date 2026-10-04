"""File-safety helpers: filename sanitization and content sniffing.

Content type is decided from the file's MAGIC BYTES, never from the
client-supplied Content-Type header (which is trivially spoofable).
"""
from __future__ import annotations

import io
import os
import re
import unicodedata
import zipfile
from typing import Optional

import olefile

DOCX_MIME = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
DOC_MIME = "application/msword"
XLS_MIME = "application/vnd.ms-excel"
PPT_MIME = "application/vnd.ms-powerpoint"

_SAFE_CHARS = re.compile(r"[^A-Za-z0-9._-]+")

# The legacy OLE Compound File Binary Format (CFBF) is a single shared
# container for Word, Excel, PowerPoint, Outlook .msg, Installer .msi, and
# more — the magic bytes alone (0xD0CF11E0...) can't tell them apart. Every
# CFBF-based Office document type stamps a distinct CLSID on its root
# storage entry, so that's what actually distinguishes a Word doc from an
# Excel workbook from a PowerPoint deck. Unrecognized/unparseable CLSIDs are
# a reason to REJECT, not to guess "probably Word" the way this module used
# to (that guess is exactly the gap being closed here).
_OLE_ROOT_CLSID_TO_MIME = {
    "00020906-0000-0000-c000-000000000046": DOC_MIME,   # Word 97-2003 Document
    "00020900-0000-0000-c000-000000000046": DOC_MIME,   # Word 6.0-95 Document
    "00020810-0000-0000-c000-000000000046": XLS_MIME,   # Excel 97-2003 Workbook (older CLSID)
    "00020820-0000-0000-c000-000000000046": XLS_MIME,   # Excel 97-2003 Workbook (newer CLSID)
    "00020821-0000-0000-c000-000000000046": XLS_MIME,   # Excel 5.0/95 Workbook
    "64818d10-4f9b-11cf-86ea-00aa00b929e8": PPT_MIME,   # PowerPoint 97-2003 Presentation
}

# Storage/stream names that indicate embedded VBA macros in an OLE
# container. Present in a "clean-looking" legacy .doc/.xls/.ppt, this means
# the file is macro-enabled — rejected here for the same reason a modern
# .docm is rejected below (macro-enabled Office documents are one of the
# most common real-world malware delivery formats).
_OLE_MACRO_MARKERS = ("macros", "vba", "_vba_project")


def sanitize_filename(raw: Optional[str], *, default: str = "file", max_length: int = 100) -> str:
    """Return a filesystem/storage-safe filename.

    - strips any directory components (`../../x`, `C:\\a\\b.pdf`)
    - transliterates to ASCII and replaces everything except
      letters, digits, dot, underscore and hyphen
    - removes leading dots (no hidden files) and collapses repeats
    - truncates to `max_length` while preserving the extension
    """
    name = (raw or "").replace("\\", "/").split("/")[-1]
    name = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode("ascii")
    name = _SAFE_CHARS.sub("_", name)
    name = re.sub(r"_{2,}", "_", name)
    name = re.sub(r"\.{2,}", ".", name)
    name = name.lstrip("._-")
    stem, ext = os.path.splitext(name)
    stem = stem.strip("._-")
    ext = ext[:10]
    if not stem:
        stem = default
    stem = stem[: max(1, max_length - len(ext))]
    return f"{stem}{ext}"


def display_filename(raw: Optional[str], *, default: str = "file", max_length: int = 200) -> str:
    """Human-readable name kept for DISPLAY only (never used as a storage
    path): directory components and control characters are removed."""
    name = (raw or "").replace("\\", "/").split("/")[-1]
    name = "".join(ch for ch in name if ch.isprintable()).strip()
    return name[:max_length] or default


def _sniff_ole_subtype(data: bytes) -> Optional[str]:
    """Distinguishes Word/Excel/PowerPoint inside a shared OLE Compound
    File container by its root storage CLSID, and rejects anything with
    embedded VBA macros. Returns None (reject) for an unparseable
    container, an unrecognized CLSID, or a macro-enabled document — never
    a best-guess MIME type, since this container format is also used by
    non-Office formats (.msi, .msg, ...) that must never be accepted as a
    "document" attachment."""
    try:
        with olefile.OleFileIO(io.BytesIO(data)) as ole:
            if any(
                any(marker in "/".join(str(p) for p in path).lower() for marker in _OLE_MACRO_MARKERS)
                for path in ole.listdir(streams=True, storages=True)
            ):
                return None  # macro-enabled legacy document: never accepted

            root_clsid = (ole.root.clsid or "").lower()
    except Exception:  # noqa: BLE001 — a malformed/truncated CFBF container is a reject, not a crash
        return None

    return _OLE_ROOT_CLSID_TO_MIME.get(root_clsid)


def sniff_content_type(data: bytes) -> Optional[str]:
    """Detect the real content type from leading bytes. Returns None
    when the type is not one we recognise."""
    if data.startswith(b"%PDF-"):
        return "application/pdf"
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if data.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if data[:6] in (b"GIF87a", b"GIF89a"):
        return "image/gif"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    if data.startswith(b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"):
        return _sniff_ole_subtype(data)
    if data.startswith(b"PK\x03\x04"):
        try:
            with zipfile.ZipFile(io.BytesIO(data)) as archive:
                names = set(archive.namelist())
        except zipfile.BadZipFile:
            return None
        if "[Content_Types].xml" in names and "word/document.xml" in names:
            if any(n.lower().endswith("vbaproject.bin") for n in names):
                return None  # macro-enabled document (.docm): never accepted
            return DOCX_MIME
        return "application/zip"
    return None


def is_legacy_ole_office_mime(content_type: Optional[str]) -> bool:
    """True for a MIME type this module only ever produces via the OLE/CFBF
    path (_sniff_ole_subtype) — used by the upload pipeline to apply a
    stricter, fail-closed malware-scan requirement to this specific format
    family regardless of the global REQUIRE_MALWARE_SCAN setting (see
    UploadService._scan): unlike a PDF or an image, a legacy Office
    container can carry macros or embedded OLE objects that are a
    materially higher real-world malware vector."""
    return content_type in (DOC_MIME, XLS_MIME, PPT_MIME)
