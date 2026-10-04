"""Itinerary module - IMPLEMENTED (Phase 3, partial).
See schemas.py, service.py (ItineraryService: item CRUD + versioning)
and validation_service.py (ItineraryValidationService: time conflicts,
duplicates, invalid coordinates, excessive daily activity).

NOT yet implemented: AI-driven generation (Phase 4/LangGraph),
automatic repair-on-validation-failure (needs AI), budget-violation
and geographic-inefficiency checks (need trip-level cost aggregation
and Maps-provider-backed scoring, respectively)."""
