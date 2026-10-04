"""Trip PDF export: hostile user text must render literally, off the event loop, into a safe file name."""
from __future__ import annotations

import io
from datetime import date, time
from types import SimpleNamespace as NS

import pytest

pytest.importorskip("reportlab")
pytest.importorskip("pypdf")

from pypdf import PdfReader  # noqa: E402

from app.modules.trips.pdf_service import PdfDay, PdfItem, PdfTrip, _build_pdf_bytes, to_pdf_data  # noqa: E402


def _text(pdf_bytes: bytes) -> str:
    return "\n".join(page.extract_text() for page in PdfReader(io.BytesIO(pdf_bytes)).pages)


def test_markup_in_user_text_is_shown_literally_never_interpreted():
    trip = PdfTrip(
        title='<b>Free</b> & <img src="/etc/hosts"/> Trip', destination="Paris <i>x", start_date=date(2026, 10, 1),
        end_date=date(2026, 10, 2), travelers=2,
        days=[PdfDay(1, date(2026, 10, 1), [PdfItem("Louvre <u>tour</u> & lunch", "Rue <br/> de Rivoli", time(9), time(11), "40 EUR")]),
              PdfDay(2, date(2026, 10, 2), [])],
    )
    pdf = _build_pdf_bytes(trip)
    text = _text(pdf)
    assert pdf.startswith(b"%PDF-")
    assert "<b>Free</b>" in text and "<u>tour</u>" in text and "No items planned yet." in text


def test_an_unbalanced_tag_in_a_title_no_longer_crashes_the_export():
    trip = PdfTrip(title="Trip <b>unclosed", destination="X", start_date=date(2026, 1, 1), end_date=date(2026, 1, 1),
                   travelers=1, days=[])
    assert _build_pdf_bytes(trip).startswith(b"%PDF-")


def test_the_snapshot_copies_plain_values_so_it_is_safe_to_hand_to_a_worker_thread():
    trip = NS(title="T", destination="D", start_date=date(2026, 1, 1), end_date=date(2026, 1, 2), travelers=3)
    item = NS(title="Museum", location_name=None, start_time=time(9), end_time=None, estimated_cost=12.5, currency="EUR")
    free = NS(title="Walk", location_name="Park", start_time=None, end_time=None, estimated_cost=None, currency=None)
    day = NS(day_number=1, date=date(2026, 1, 1), items=[item, free])
    data = to_pdf_data(trip, [day])
    assert data.days[0].items[0] == PdfItem("Museum", "", time(9), None, "12.5 EUR")
    assert data.days[0].items[1].cost == "" and data.travelers == 3
