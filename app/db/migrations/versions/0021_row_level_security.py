"""Row-Level Security policies (Master Prompt §5, §64) — defense in depth

Adds a SECOND, database-enforced layer of authorization underneath the application's own
checks (TripService.get_trip_authorized, etc.). It protects against the class of bug where an
application-layer check is missing or wrong — a query that forgets a WHERE clause still cannot
return another user's rows once RLS is enabled.

HOW IT WORKS: every policy compares a row's owner to `current_setting('app.user_id', true)`, a
per-connection session variable. The application sets it once per request, in
`get_current_user` (see app/db/rls.py `set_rls_user`), via `SET LOCAL` — scoped to that
request's transaction, reset automatically afterwards.

NON-BREAKING BY DEFAULT: every policy also allows the row through when the setting is UNSET
(empty string) — `current_setting('app.user_id', true) = ''`. This means:
  * migrations, the admin/analytics/worker connections, and any session that never calls
    `set_rls_user` keep exactly their current (full) access — nothing here changes behaviour
    until `RLS_ENFORCE=true` AND the application actually sets the session variable;
  * once enforced, the ORDINARY connection pool used by end-user requests is scoped to that
    user's own data even if a WHERE clause is ever missing from application code.
Postgres's `rowsecurity` is NOT enforced against the table owner (the migration/app DB role) by
default — `FORCE ROW LEVEL SECURITY` would be needed for that, which is deliberately NOT applied
here since Alembic and admin tooling run as that same role.

Covers the directly user-owned tables plus trips (owner OR active member — collaboration) and
everything hanging off a trip (days/items/versions/notes/costs/routes/preferences) via a
subquery against `trips`. Provider caches, DailyMetric, admin/RBAC tables and other
platform-wide data are intentionally NOT user-scoped.

Revision ID: 0021_row_level_security
Revises: 0020_usage_and_durable_cache
Create Date: 2026-09-22

"""
from typing import Sequence, Union

from alembic import op

revision: str = "0021_row_level_security"
down_revision: Union[str, None] = "0020_usage_and_durable_cache"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_SETTING = "current_setting('app.user_id', true)"
_UNSET = f"({_SETTING} = '')"
_SELF = f"({_SETTING} = user_id::text)"

# table -> USING expression (what a row must satisfy to be visible/writable)
_DIRECT = {
    "user_memories": _SELF,
    "conversations": _SELF,
    "notifications": _SELF,
    "user_sessions": _SELF,
    "user_preferences": _SELF,
    "attachments": _SELF,
    "discovery_searches": _SELF,
    "trip_preferences_profiles": None,  # placeholder, not a real table — removed below
}
del _DIRECT["trip_preferences_profiles"]

# trip_id-scoped tables: visible when their trip is (via the trips policy's own condition, repeated
# here since RLS policies cannot reference another table's policy directly).
_TRIP_OWNED_EXPR = (
    f"EXISTS (SELECT 1 FROM trips t WHERE t.id = trip_id AND ({_SETTING} = t.owner_id::text OR "
    f"EXISTS (SELECT 1 FROM trip_members m WHERE m.trip_id = t.id AND m.user_id::text = {_SETTING})))"
)
_TRIP_OWNED = {"trip_days", "trip_versions", "trip_notes", "trip_costs", "trip_routes", "trip_preferences"}

_TRIPS_EXPR = (
    f"({_SETTING} = owner_id::text OR EXISTS "
    f"(SELECT 1 FROM trip_members m WHERE m.trip_id = trips.id AND m.user_id::text = {_SETTING}))"
)

# child-of-child: trip_items hangs off trip_days, which hangs off trips.
_TRIP_ITEMS_EXPR = (
    f"EXISTS (SELECT 1 FROM trip_days d JOIN trips t ON t.id = d.trip_id WHERE d.id = trip_day_id AND "
    f"({_SETTING} = t.owner_id::text OR EXISTS (SELECT 1 FROM trip_members m WHERE m.trip_id = t.id AND "
    f"m.user_id::text = {_SETTING})))"
)
# messages hang off conversations.
_MESSAGES_EXPR = f"EXISTS (SELECT 1 FROM conversations c WHERE c.id = conversation_id AND {_SETTING} = c.user_id::text)"
# discovery_results hang off discovery_searches.
_DISCOVERY_RESULTS_EXPR = f"EXISTS (SELECT 1 FROM discovery_searches s WHERE s.id = search_id AND {_SETTING} = s.user_id::text)"


_ALL_TABLES = list(_DIRECT) + sorted(_TRIP_OWNED) + ["trips", "trip_items", "messages", "discovery_results"]


def _expr_for(table: str) -> str:
    if table in _DIRECT:
        return _DIRECT[table]
    if table in _TRIP_OWNED:
        return _TRIP_OWNED_EXPR
    return {"trips": _TRIPS_EXPR, "trip_items": _TRIP_ITEMS_EXPR, "messages": _MESSAGES_EXPR,
           "discovery_results": _DISCOVERY_RESULTS_EXPR}[table]


def upgrade() -> None:
    for table in _ALL_TABLES:
        expr = _expr_for(table)
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
        op.execute(
            f"CREATE POLICY {table}_owner_access ON {table} FOR ALL USING ({_UNSET} OR {expr}) WITH CHECK ({_UNSET} OR {expr})"
        )


def downgrade() -> None:
    for table in reversed(_ALL_TABLES):
        op.execute(f"DROP POLICY IF EXISTS {table}_owner_access ON {table}")
        op.execute(f"ALTER TABLE {table} DISABLE ROW LEVEL SECURITY")
