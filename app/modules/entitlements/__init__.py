"""Entitlements module - IMPLEMENTED (Phase 6).
See schemas.py and service.py (EntitlementService: the single place
that resolves per-user feature access - override > trial >
subscription > free tier - per Master Blueprint section 48's explicit
instruction to use an EntitlementService rather than hardcoded
`if premium:` checks).

Actually used outside this module too: see
app/api/dependencies.py's require_feature() dependency factory,
applied to POST /companion/conversations/{id}/messages as a concrete
example of gating a real endpoint on a resolved entitlement rather
than a hardcoded check."""
