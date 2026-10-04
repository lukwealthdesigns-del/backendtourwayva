from app.modules.attachments.structured_extraction import extract_structured_fields


def test_empty_text_returns_empty_lists():
    result = extract_structured_fields("")
    assert result == {"dates": [], "amounts": [], "confirmation_codes": []}


def test_extracts_iso_date():
    result = extract_structured_fields("Check-in: 2026-10-05")
    assert "2026-10-05" in result["dates"]


def test_extracts_written_date():
    result = extract_structured_fields("Departure Date: Oct 5, 2026")
    assert "Oct 5, 2026" in result["dates"]


def test_extracts_currency_amount():
    result = extract_structured_fields("Total: USD 450.00")
    assert "USD 450.00" in result["amounts"]


def test_extracts_confirmation_code_with_colon():
    result = extract_structured_fields("Booking Confirmation: ABC12XYZ9")
    assert "ABC12XYZ9" in result["confirmation_codes"]


def test_extracts_confirmation_code_with_no_label():
    result = extract_structured_fields("Reference No: XY7788")
    assert "XY7788" in result["confirmation_codes"]


def test_does_not_capture_plain_words_as_codes():
    """Regression test: an earlier version of this regex matched the
    label word itself (e.g. 'Confirmation') as a fake code because
    re.IGNORECASE made the character class match letters too. Fixed
    by requiring at least one digit in the captured code."""
    result = extract_structured_fields("Confirmation pending, no code yet.")
    assert "Confirmation" not in result["confirmation_codes"]


def test_deduplicates_repeated_matches():
    result = extract_structured_fields("2026-10-05 and again 2026-10-05")
    assert result["dates"].count("2026-10-05") == 1
