"""
Trip PDF generation (Master Blueprint's Trip PDFs storage use case,
Phase 1 adjustment #1 — the endpoint existed with nothing that ever
generated a PDF to upload to it. This closes that gap).

Builds a real PDF (day-by-day itinerary, times, locations, costs)
with `reportlab`, uploads it to Supabase Storage via the same
TRIP_PDF storage route Phase 1 set up, and records it as an
Attachment row (trip_id set) so it shows up alongside other trip
documents. Generated PDFs skip the extraction pipeline — there's
nothing to extract from a document Tour-Wayva itself produced.
"""
from __future__ import annotations

import asyncio
import io
import uuid
from dataclasses import dataclass
from datetime import date, datetime, time, timezone
from typing import Optional

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.constants import AttachmentCategory, AttachmentExtractionStatus, UploadUseCase
from app.db.models.attachment import Attachment
from app.modules.itinerary.service import ItineraryService
from app.modules.trips.service import TripService
from app.providers.storage.factory import get_storage_provider
from app.repositories.attachment_repository import AttachmentRepository
from app.utils.files import display_filename, sanitize_filename
from app.utils.html import esc


@dataclass(frozen=True)
class PdfItem:
    title: str
    location: str
    start: Optional[time]
    end: Optional[time]
    cost: str


@dataclass(frozen=True)
class PdfDay:
    number: int
    date: date
    items: list[PdfItem]


@dataclass(frozen=True)
class PdfTrip:
    title: str
    destination: str
    start_date: date
    end_date: date
    travelers: int
    days: list[PdfDay]


def to_pdf_data(trip, days) -> PdfTrip:
    """Plain data snapshot of the ORM objects: safe to hand to a worker thread (no lazy loads,
    no session access from another thread)."""
    return PdfTrip(
        title=trip.title, destination=trip.destination, start_date=trip.start_date, end_date=trip.end_date,
        travelers=trip.travelers,
        days=[
            PdfDay(
                number=day.day_number, date=day.date,
                items=[
                    PdfItem(
                        title=item.title, location=item.location_name or "", start=item.start_time, end=item.end_time,
                        cost=f"{item.estimated_cost:g} {item.currency}" if item.estimated_cost else "",
                    )
                    for item in getattr(day, "items", [])
                ],
            )
            for day in days
        ],
    )


def _build_pdf_bytes(trip: PdfTrip) -> bytes:
    """CPU-bound (reportlab): call via asyncio.to_thread, never on the event loop.

    Every user-supplied string goes through `esc()` because reportlab's Paragraph parses XML-like
    markup: an unescaped title such as `<b>` breaks the build, and tags like `<img src=...>` would
    make the renderer open files/URLs the author chose."""
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import LETTER
    from reportlab.lib.styles import getSampleStyleSheet
    from reportlab.lib.units import inch
    from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

    buffer = io.BytesIO()
    doc = SimpleDocTemplate(buffer, pagesize=LETTER, topMargin=0.75 * inch, bottomMargin=0.75 * inch)
    styles = getSampleStyleSheet()
    story = []

    story.append(Paragraph(esc(trip.title), styles["Title"]))
    story.append(
        Paragraph(
            f"{esc(trip.destination)} &middot; {trip.start_date.isoformat()} to {trip.end_date.isoformat()} "
            f"&middot; {trip.travelers} traveler(s)",
            styles["Normal"],
        )
    )
    story.append(Spacer(1, 0.25 * inch))

    for day in trip.days:
        story.append(Paragraph(f"Day {day.number} \u2014 {day.date.isoformat()}", styles["Heading2"]))
        if not day.items:
            story.append(Paragraph("No items planned yet.", styles["Italic"]))
        else:
            table_data = [["Time", "Item", "Location", "Cost"]]
            for item in day.items:
                time_range = ""
                if item.start:
                    time_range = item.start.strftime("%H:%M")
                    if item.end:
                        time_range += f" - {item.end.strftime('%H:%M')}"
                # Table cells are wrapped in Paragraph so long text wraps; hence escaped too.
                table_data.append([time_range, Paragraph(esc(item.title), styles["Normal"]),
                                   Paragraph(esc(item.location), styles["Normal"]), item.cost])

            table = Table(table_data, colWidths=[1.0 * inch, 2.5 * inch, 2.0 * inch, 1.0 * inch])
            table.setStyle(
                TableStyle(
                    [
                        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#2b2b2b")),
                        ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
                        ("FONTSIZE", (0, 0), (-1, -1), 9),
                        ("GRID", (0, 0), (-1, -1), 0.5, colors.grey),
                        ("VALIGN", (0, 0), (-1, -1), "TOP"),
                    ]
                )
            )
            story.append(table)
        story.append(Spacer(1, 0.2 * inch))

    doc.build(story)
    return buffer.getvalue()


class TripPDFService:
    def __init__(self, db: AsyncSession):
        self.db = db
        self.trip_service = TripService(db)
        self.itinerary_service = ItineraryService(db)
        self.attachment_repo = AttachmentRepository(db)

    async def generate_and_upload(self, *, trip_id: uuid.UUID, user_id: uuid.UUID) -> Attachment:
        trip = await self.trip_service.get_trip_authorized(trip_id=trip_id, user_id=user_id)
        days = await self.itinerary_service.get_full_itinerary(trip_id)

        # Snapshot to plain data, then render off the event loop (reportlab is CPU-bound).
        pdf_bytes = await asyncio.to_thread(_build_pdf_bytes, to_pdf_data(trip, days))

        # The trip title is user-controlled: sanitize it for the storage path and make the key unique.
        safe_title = sanitize_filename(trip.title, default="trip", max_length=60).rsplit(".", 1)[0]
        filename = f"{safe_title}_{datetime.now(timezone.utc).strftime('%Y%m%d%H%M%S')}_{uuid.uuid4().hex[:8]}.pdf"
        provider = get_storage_provider(UploadUseCase.TRIP_PDF)
        upload_result = await provider.upload(
            file_bytes=pdf_bytes, filename=filename, content_type="application/pdf",
            folder=f"trips/{trip_id}", user_id=str(user_id),
        )

        attachment = Attachment(
            user_id=user_id, trip_id=trip_id, original_filename=display_filename(trip.title + ".pdf", default="trip.pdf"),
            content_type="application/pdf",
            storage_key=upload_result.storage_key, url=upload_result.url,
            extraction_status=AttachmentExtractionStatus.COMPLETED, extracted_text=None,
            category=AttachmentCategory.TRIP_PDF,
        )
        await self.attachment_repo.create(attachment)
        await self.db.commit()
        return attachment
