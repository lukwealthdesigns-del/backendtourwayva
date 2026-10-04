"""Notifications module - IMPLEMENTED (Phase 8).
See schemas.py and service.py (NotificationService: notify() is the
single entry point other services call - in-app row + optional
email, gated by per-user NotificationPreference). Wired into
InvitationService (trip invitation), PendingChangeService (itinerary
change decided), and AdminMessagingService (admin message)."""
