"""Trips module - IMPLEMENTED (Phase 3 + Phase 5 member management +
Phase 8 extras/PDF export).
See schemas.py and service.py (TripService: creation, day scaffolding,
ownership/membership authorization checkpoint used across every
trip-related module, plus member list/change-role/remove and
invitation-acceptance hookup for Phase 5 collaboration).

Phase 8 additions: extras_schemas.py + extras_service.py
(TripExtrasService: real behavior for trip_notes/trip_costs/
trip_routes - previously schema-only) and pdf_service.py
(TripPDFService: generates a real itinerary PDF with reportlab,
uploads it via the existing Supabase trip-pdf storage route)."""
