"""UploadService malware-scan gating (Master Prompt §42, §108).

Covers both the pre-existing global REQUIRE_MALWARE_SCAN fail-closed rule
and the new rule added alongside the OLE CLSID-sniffing fix: legacy OLE
Office attachments (.doc/.xls/.ppt) always require a reachable scanner,
independent of the global setting, because that shared container format
is a common real-world macro/malware vector.
"""
import asyncio
import io
from typing import Optional

import pytest

from app.core.config import settings
from app.core.exceptions import ProviderUnavailableError, ValidationAppError
from app.modules.uploads.service import UploadService
from app.providers.malware.interface import MalwareScanner, ScanResult, ScanStatus


def _run(coro):
    return asyncio.run(coro)


class _FakeUploadFile:
    """Duck-types just what UploadService.read_validated touches."""

    def __init__(self, content: bytes, filename: str = "file.pdf"):
        self.filename = filename
        self._buf = io.BytesIO(content)

    async def read(self, n: int) -> bytes:
        return self._buf.read(n)


class _StubScanner(MalwareScanner):
    def __init__(self, *, result: Optional[ScanResult] = None, raise_unavailable: bool = False):
        self._result = result
        self._raise_unavailable = raise_unavailable
        self.calls = 0

    async def scan(self, data: bytes) -> ScanResult:
        self.calls += 1
        if self._raise_unavailable:
            raise ProviderUnavailableError("scanner down")
        return self._result or ScanResult(status=ScanStatus.CLEAN)


_PDF = b"%PDF-1.7\n" + b"0" * 16

# A .doc-shaped OLE file, magic bytes only — content sniffing itself is
# monkeypatched out in these tests so we can drive the scan-gating logic
# in isolation from the CLSID parser (covered separately in test_files_util.py).
_OLE = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1" + b"\x00" * 504


def _patch_doc_sniff(monkeypatch):
    """Makes sniff_content_type report DOC_MIME for our fake OLE bytes,
    without depending on the real CLSID parser being exercised here."""
    import app.modules.uploads.service as upload_service_module

    monkeypatch.setattr(
        upload_service_module, "sniff_content_type",
        lambda data: "application/msword" if data == _OLE else (
            "application/pdf" if data.startswith(b"%PDF-") else None
        ),
    )


@pytest.fixture(autouse=True)
def _reset_require_malware_scan():
    original = settings.REQUIRE_MALWARE_SCAN
    yield
    settings.REQUIRE_MALWARE_SCAN = original


def test_pdf_upload_succeeds_with_no_scanner_when_scanning_not_required(monkeypatch):
    _patch_doc_sniff(monkeypatch)
    settings.REQUIRE_MALWARE_SCAN = False
    service = UploadService(scanner=None)
    result = _run(service.read_validated(_FakeUploadFile(_PDF), max_mb=5, allowed_mimes={"application/pdf"}))
    assert result.content_type == "application/pdf"


def test_pdf_upload_rejected_with_no_scanner_when_scanning_globally_required(monkeypatch):
    _patch_doc_sniff(monkeypatch)
    settings.REQUIRE_MALWARE_SCAN = True
    service = UploadService(scanner=None)
    with pytest.raises(ProviderUnavailableError):
        _run(service.read_validated(_FakeUploadFile(_PDF), max_mb=5, allowed_mimes={"application/pdf"}))


def test_doc_upload_rejected_with_no_scanner_even_when_scanning_globally_not_required(monkeypatch):
    # This is the new rule: legacy OLE Office formats fail closed regardless
    # of REQUIRE_MALWARE_SCAN.
    _patch_doc_sniff(monkeypatch)
    settings.REQUIRE_MALWARE_SCAN = False
    service = UploadService(scanner=None)
    with pytest.raises(ProviderUnavailableError):
        _run(service.read_validated(_FakeUploadFile(_OLE, "resume.doc"), max_mb=5, allowed_mimes={"application/msword"}))


def test_doc_upload_rejected_when_scanner_unreachable_even_though_pdf_would_be_allowed_through(monkeypatch):
    _patch_doc_sniff(monkeypatch)
    settings.REQUIRE_MALWARE_SCAN = False
    scanner = _StubScanner(raise_unavailable=True)
    service = UploadService(scanner=scanner)

    # A PDF tolerates the scanner being briefly unreachable (global flag is off) ...
    result = _run(service.read_validated(_FakeUploadFile(_PDF), max_mb=5, allowed_mimes={"application/pdf"}))
    assert result.content_type == "application/pdf"

    # ... but a .doc does not, even with the exact same unreachable scanner.
    with pytest.raises(ProviderUnavailableError):
        _run(service.read_validated(_FakeUploadFile(_OLE, "resume.doc"), max_mb=5, allowed_mimes={"application/msword"}))


def test_doc_upload_succeeds_when_scanner_is_reachable_and_clean(monkeypatch):
    _patch_doc_sniff(monkeypatch)
    settings.REQUIRE_MALWARE_SCAN = False
    scanner = _StubScanner(result=ScanResult(status=ScanStatus.CLEAN))
    service = UploadService(scanner=scanner)
    result = _run(service.read_validated(_FakeUploadFile(_OLE, "resume.doc"), max_mb=5, allowed_mimes={"application/msword"}))
    assert result.content_type == "application/msword"
    assert scanner.calls == 1


def test_infected_file_is_rejected_regardless_of_type(monkeypatch):
    _patch_doc_sniff(monkeypatch)
    scanner = _StubScanner(result=ScanResult(status=ScanStatus.INFECTED, signature="Eicar-Test-Signature"))
    service = UploadService(scanner=scanner)
    with pytest.raises(ValidationAppError):
        _run(service.read_validated(_FakeUploadFile(_PDF), max_mb=5, allowed_mimes={"application/pdf"}))
