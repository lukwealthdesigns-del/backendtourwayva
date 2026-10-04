"""Attachments module - IMPLEMENTED (Phase 8).
See schemas.py, service.py (AttachmentService: upload via existing
Supabase-backed UploadService, then extraction/OCR + structured-field
heuristics + confirm + trip-linking - the full Blueprint section 42
pipeline), and structured_extraction.py (regex-based date/amount/
confirmation-code detection over extracted text - verified working
against sample text in this delivery).

Extraction runs synchronously today; moving it to the existing Celery
worker for large files is a reasonable next hardening step."""
