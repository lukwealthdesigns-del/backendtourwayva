import pytest

from app.core.exceptions import ProviderUnavailableError
from app.providers.malware.clamav_provider import parse_clamd_response
from app.providers.malware.interface import ScanStatus


def test_clean_reply():
    assert parse_clamd_response(b"stream: OK\x00").status == ScanStatus.CLEAN


def test_infected_reply_extracts_signature():
    result = parse_clamd_response(b"stream: Eicar-Test-Signature FOUND\x00")
    assert result.status == ScanStatus.INFECTED
    assert result.signature == "Eicar-Test-Signature"


def test_error_reply_is_treated_as_scanner_failure_not_clean():
    with pytest.raises(ProviderUnavailableError):
        parse_clamd_response(b"INSTREAM size limit exceeded. ERROR\x00")
