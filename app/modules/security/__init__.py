"""Security module - IMPLEMENTED (Phase 8).
See service.py (SecurityService: IP blocking with expiry,
login-attempt persistence, security event log - the durable
counterpart to app/core/rate_limit.py's real-time Redis lockout).
Wired into AuthService.login (checks IP blocks, persists every login
attempt) and exposed via GET/POST /admin/security/*."""
