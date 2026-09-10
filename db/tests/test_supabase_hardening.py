"""Migration 0010 must leave no engine table reachable by Supabase's PostgREST roles (spec §22C)."""

from __future__ import annotations


def _tables(conn) -> list[dict]:
    return conn.execute(
        "select c.relname, c.relrowsecurity, c.relforcerowsecurity from pg_class c "
        "join pg_namespace n on n.oid=c.relnamespace where n.nspname='public' and c.relkind='r' order by 1"
    ).fetchall()


def test_row_level_security_is_enabled_on_every_table(conn):
    off = [t["relname"] for t in _tables(conn) if not t["relrowsecurity"]]
    assert off == [], f"row-level security is off on: {off}"


def test_every_table_has_at_least_one_policy(conn):
    counts = {
        r["tablename"]: r["n"]
        for r in conn.execute("select tablename, count(*) as n from pg_policies where schemaname='public' group by 1").fetchall()
    }
    missing = [t["relname"] for t in _tables(conn) if counts.get(t["relname"], 0) == 0]
    assert missing == [], f"tables with RLS on but no policy are unreachable by everyone: {missing}"


def test_patient_tables_keep_forced_rls(conn):
    forced = {t["relname"] for t in _tables(conn) if t["relforcerowsecurity"]}
    assert {"case_snapshot", "case_observation", "plan_version"} <= forced


def test_catalog_tables_are_not_forced_so_the_owner_can_migrate(conn):
    forced = {t["relname"] for t in _tables(conn) if t["relforcerowsecurity"]}
    assert "exercise_variant_version" not in forced and "rights_grant" not in forced


def test_backend_role_can_still_reach_every_table(conn):
    for t in _tables(conn):
        ok = conn.execute("select has_table_privilege('moveai_app', %s, 'select') as ok", (f"public.{t['relname']}",)).fetchone()["ok"]
        assert ok, f"moveai_app lost access to {t['relname']}"


def test_exposed_roles_have_no_access_when_they_exist(conn):
    """On Supabase these roles exist and must hold nothing. Locally they do not exist, so this is a no-op."""
    roles = {r["rolname"] for r in conn.execute("select rolname from pg_roles").fetchall()}
    for role in ("anon", "authenticated"):
        if role not in roles:
            continue
        assert not conn.execute("select has_schema_privilege(%s,'public','usage') as ok", (role,)).fetchone()["ok"]
        for t in _tables(conn):
            for priv in ("select", "insert", "update", "delete"):
                got = conn.execute("select has_table_privilege(%s,%s,%s) as ok", (role, f"public.{t['relname']}", priv)).fetchone()["ok"]
                assert not got, f"{role} can {priv} on {t['relname']}"


def test_auth_user_mapping_column_exists(conn):
    row = conn.execute(
        "select data_type from information_schema.columns where table_name='app_user' and column_name='auth_user_id'"
    ).fetchone()
    assert row and row["data_type"] == "uuid"
