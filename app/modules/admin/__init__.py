"""Admin module - IMPLEMENTED (Phase 7).
See schemas.py and:
  - admin_service.py (AdminService: RBAC resolution/permission checks
    via app/core/admin_permissions.py, admin roster management
    restricted to SUPER_ADMIN, append-only audit logging)
  - user_management_service.py (UserManagementService: search, detail
    with subscription/trial status, block/unblock, soft-delete,
    session revocation)
  - messaging_service.py (AdminMessagingService: direct admin-to-user
    messages, in-app and/or email)
  - broadcast_service.py + app/workers/broadcast_tasks.py
    (BroadcastService: creates a job and returns immediately; actual
    segment resolution and sending runs in the Celery worker
    container, never inside the request - Blueprint section 53)

Session revocation (Blueprint sections 51, 77) is a real, working
mechanism: User.sessions_invalidated_at, checked on every request by
get_current_user (app/api/dependencies.py) against each token's `iat`
claim - not a stub.

RBAC permissions are a code-level dict
(app/core/admin_permissions.py), not the fully dynamic
admin_permissions/admin_role_permissions join-table schema the
blueprint's data model section sketches - see that file's docstring
for why, and how it would migrate if runtime-configurable RBAC
becomes a real need.

NOT yet implemented: feature-flag management UI/endpoints beyond what
Phase 6 already exposes (app/modules/entitlements), full analytics
(AI cost, provider usage, revenue - app/modules/analytics stays
scaffolded), and the account-deletion workflow's anonymization step
(Blueprint section 77 - admin delete_user here is the same soft-delete
block_user uses, just a different status)."""
