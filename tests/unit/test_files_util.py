"""Filename sanitization and magic-byte content sniffing (upload safety)."""
import io
import types
import zipfile

import app.utils.files as files_module
from app.utils.files import (
    DOC_MIME,
    DOCX_MIME,
    PPT_MIME,
    XLS_MIME,
    display_filename,
    is_legacy_ole_office_mime,
    sanitize_filename,
    sniff_content_type,
)


def _zip(names):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        for n in names:
            z.writestr(n, "x")
    return buf.getvalue()


def test_sanitize_strips_directory_traversal():
    assert sanitize_filename("../../etc/passwd") == "passwd"
    assert sanitize_filename("C:\\Users\\me\\Report (final).pdf") == "Report_final.pdf"


def test_sanitize_removes_hidden_and_unsafe_chars():
    assert sanitize_filename(".htaccess") == "htaccess"
    assert sanitize_filename("naïve café.PNG") == "naive_cafe.PNG"
    assert sanitize_filename("a b\x00c.pdf") == "a_b_c.pdf"


def test_sanitize_falls_back_to_default_and_truncates_keeping_extension():
    assert sanitize_filename(None) == "file"
    assert sanitize_filename("") == "file"
    long_name = sanitize_filename("a" * 400 + ".pdf", max_length=50)
    assert len(long_name) <= 50 and long_name.endswith(".pdf")


def test_display_filename_keeps_readable_name_but_no_paths_or_control_chars():
    assert display_filename("../x/Booking Confirmation (Air Peace).pdf") == "Booking Confirmation (Air Peace).pdf"
    assert display_filename("a\x00b\n.pdf") == "ab.pdf"
    assert display_filename(None) == "file"


def test_sniff_recognises_supported_types():
    assert sniff_content_type(b"%PDF-1.7\n...") == "application/pdf"
    assert sniff_content_type(b"\x89PNG\r\n\x1a\n" + b"0" * 8) == "image/png"
    assert sniff_content_type(b"\xff\xd8\xff\xe0" + b"0" * 8) == "image/jpeg"
    assert sniff_content_type(b"RIFF\x00\x00\x00\x00WEBPVP8 ") == "image/webp"
    assert sniff_content_type(b"GIF89a....") == "image/gif"


def test_sniff_rejects_executables_and_text_regardless_of_extension():
    assert sniff_content_type(b"MZ\x90\x00\x03") is None          # Windows executable
    assert sniff_content_type(b"#!/bin/sh\nrm -rf /") is None
    assert sniff_content_type(b"<html><script>alert(1)</script>") is None


def test_sniff_docx_vs_generic_zip_vs_macro_enabled():
    assert sniff_content_type(_zip(["[Content_Types].xml", "word/document.xml"])) == DOCX_MIME
    assert sniff_content_type(_zip(["notes.txt"])) == "application/zip"
    assert sniff_content_type(
        _zip(["[Content_Types].xml", "word/document.xml", "word/vbaProject.bin"])
    ) is None  # .docm must never be accepted as a document


# --- Legacy OLE (.doc/.xls/.ppt share one container format) ---
#
# olefile only READS compound files, it can't author a minimal valid one
# for a test fixture, so these tests stub `olefile.OleFileIO` itself
# (a context manager exposing .root.clsid and .listdir()) rather than
# constructing real CFBF bytes — what matters here is _sniff_ole_subtype's
# CLSID-mapping and macro-detection logic, not olefile's own parser.

_OLE_MAGIC = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1" + b"\x00" * 504  # header-sized filler


class _FakeOleFile:
    def __init__(self, clsid: str, entries: list[list[str]] | None = None):
        self.root = types.SimpleNamespace(clsid=clsid)
        self._entries = entries or []

    def listdir(self, streams=True, storages=False):
        return self._entries

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def _patched(monkeypatch, fake: "_FakeOleFile | Exception"):
    def _factory(_data):
        if isinstance(fake, Exception):
            raise fake
        return fake

    monkeypatch.setattr(files_module.olefile, "OleFileIO", _factory)


def test_sniff_ole_word_document(monkeypatch):
    _patched(monkeypatch, _FakeOleFile("00020906-0000-0000-C000-000000000046"))
    assert sniff_content_type(_OLE_MAGIC) == DOC_MIME


def test_sniff_ole_excel_workbook_is_not_mislabeled_as_word(monkeypatch):
    # This is the exact bug being closed: previously EVERY OLE file, Excel
    # included, was returned as DOC_MIME (and therefore accepted, since
    # application/msword is in the default allow-list).
    _patched(monkeypatch, _FakeOleFile("00020820-0000-0000-C000-000000000046"))
    result = sniff_content_type(_OLE_MAGIC)
    assert result == XLS_MIME
    assert result != DOC_MIME


def test_sniff_ole_powerpoint_presentation(monkeypatch):
    _patched(monkeypatch, _FakeOleFile("64818D10-4F9B-11CF-86EA-00AA00B929E8"))
    assert sniff_content_type(_OLE_MAGIC) == PPT_MIME


def test_sniff_ole_unrecognized_clsid_is_rejected_not_guessed(monkeypatch):
    # An .msi, .msg, or anything else sharing the CFBF container must never
    # fall through to a "probably Word" guess.
    _patched(monkeypatch, _FakeOleFile("00000000-0000-0000-0000-000000000000"))
    assert sniff_content_type(_OLE_MAGIC) is None


def test_sniff_ole_with_macros_is_rejected_even_with_a_recognized_clsid(monkeypatch):
    _patched(monkeypatch, _FakeOleFile(
        "00020906-0000-0000-C000-000000000046", entries=[["Macros", "VBA", "dir"]]
    ))
    assert sniff_content_type(_OLE_MAGIC) is None


def test_sniff_ole_unparseable_container_is_rejected_not_crashed(monkeypatch):
    _patched(monkeypatch, OSError("not a valid OLE2 structured storage file"))
    assert sniff_content_type(_OLE_MAGIC) is None


def test_is_legacy_ole_office_mime():
    assert is_legacy_ole_office_mime(DOC_MIME) is True
    assert is_legacy_ole_office_mime(XLS_MIME) is True
    assert is_legacy_ole_office_mime(PPT_MIME) is True
    assert is_legacy_ole_office_mime(DOCX_MIME) is False
    assert is_legacy_ole_office_mime("application/pdf") is False
    assert is_legacy_ole_office_mime(None) is False
