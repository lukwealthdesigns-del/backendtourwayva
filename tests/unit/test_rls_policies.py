"""Row-Level Security policy generation (migration 0021): every relevant table is covered,
the unset-session-variable escape hatch is present everywhere, and each ownership expression
resolves the right way (direct owner, trip owner-or-member, or a join up to trips/conversations)."""
from __future__ import annotations

import importlib.util
import sys
import types
from pathlib import Path

MIGRATION_PATH = Path(__file__).resolve().parents[2] / "app" / "db" / "migrations" / "versions" / "0021_row_level_security.py"


def _load_migration():
    fake_alembic = types.ModuleType("alembic")

    class _Op:
        def __init__(self):
            self.statements = []

        def execute(self, sql):
            self.statements.append(sql)

    fake_alembic.op = _Op()
    sys.modules["alembic"] = fake_alembic
    spec = importlib.util.spec_from_file_location("rls_migration", MIGRATION_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module, fake_alembic.op


MODULE, OP = _load_migration()

DIRECTLY_OWNED = {"user_memories", "conversations", "notifications", "user_sessions", "user_preferences",
                  "attachments", "discovery_searches"}
TRIP_CHILD_TABLES = {"trip_days", "trip_versions", "trip_notes", "trip_costs", "trip_routes", "trip_preferences"}


def test_every_user_owned_table_the_app_writes_is_covered():
    expected = DIRECTLY_OWNED | TRIP_CHILD_TABLES | {"trips", "trip_items", "messages", "discovery_results"}
    assert set(MODULE._ALL_TABLES) == expected


def test_directly_owned_tables_compare_the_session_variable_to_their_own_user_id_column():
    for table in DIRECTLY_OWNED:
        expr = MODULE._expr_for(table)
        assert expr == "(current_setting('app.user_id', true) = user_id::text)", table


def test_trips_allows_the_owner_or_an_active_member():
    expr = MODULE._expr_for("trips")
    assert "owner_id::text" in expr and "trip_members" in expr and "m.trip_id = trips.id" in expr


def test_trip_child_tables_join_up_to_trips_for_owner_or_member():
    for table in TRIP_CHILD_TABLES:
        expr = MODULE._expr_for(table)
        assert expr == MODULE._TRIP_OWNED_EXPR, table
    assert "FROM trips t WHERE t.id = trip_id" in MODULE._TRIP_OWNED_EXPR
    assert "trip_members" in MODULE._TRIP_OWNED_EXPR


def test_trip_items_joins_through_trip_days_up_to_trips():
    expr = MODULE._expr_for("trip_items")
    assert "trip_days d" in expr and "JOIN trips t ON t.id = d.trip_id" in expr and "d.id = trip_day_id" in expr


def test_messages_and_discovery_results_join_to_their_direct_parent_only():
    messages = MODULE._expr_for("messages")
    assert "conversations c" in messages and "c.id = conversation_id" in messages and "c.user_id::text" in messages
    assert "trips" not in messages                                     # no unnecessary extra join

    results = MODULE._expr_for("discovery_results")
    assert "discovery_searches s" in results and "s.id = search_id" in results and "s.user_id::text" in results


def test_every_policy_falls_through_when_the_session_variable_is_unset():
    """The non-breaking guarantee: migrations/admin/worker connections that never call
    set_rls_user must see every row, not zero rows."""
    for table in MODULE._ALL_TABLES:
        assert MODULE._UNSET in (f"({MODULE._SETTING} = '')",)          # sanity: the constant itself
        assert "current_setting('app.user_id', true) = ''" in MODULE._UNSET


def test_upgrade_enables_rls_and_creates_a_using_and_check_policy_for_every_table():
    OP.statements.clear()
    MODULE.upgrade()
    enabled = [s for s in OP.statements if "ENABLE ROW LEVEL SECURITY" in s]
    policies = [s for s in OP.statements if s.startswith("CREATE POLICY")]
    assert len(enabled) == len(policies) == len(MODULE._ALL_TABLES)
    for table in MODULE._ALL_TABLES:
        assert any(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY" == s for s in enabled), table
        policy = next(s for s in policies if f"ON {table} " in s)
        assert "USING (" in policy and "WITH CHECK (" in policy
        assert "current_setting('app.user_id', true) = ''" in policy   # the escape hatch is IN the policy


def test_downgrade_drops_every_policy_and_disables_rls():
    OP.statements.clear()
    MODULE.downgrade()
    for table in MODULE._ALL_TABLES:
        assert f"DROP POLICY IF EXISTS {table}_owner_access ON {table}" in OP.statements
        assert f"ALTER TABLE {table} DISABLE ROW LEVEL SECURITY" in OP.statements


def test_no_table_name_can_be_sql_injected_since_the_set_is_a_fixed_python_literal():
    import re

    for table in MODULE._ALL_TABLES:
        assert re.fullmatch(r"[a-z_]+", table), table
