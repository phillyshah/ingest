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


# --------------------------------------------------------------------------------------------------------------
# Reproduce Supabase's real shape locally.
#
# The suite previously tested a database whose `public` schema had been dropped and recreated, which silently
# removes the PUBLIC pseudo-role's USAGE grant. Supabase uses the original schema, so two defects in migration 0010
# were invisible here and only surfaced on the real project. These tests recreate those conditions.
# --------------------------------------------------------------------------------------------------------------
import pathlib

MIGRATIONS = pathlib.Path(__file__).resolve().parents[2] / "db" / "migrations"
EXPOSED = ("anon", "authenticated")
PRIVS = ("select", "insert", "update", "delete")


def _make_it_look_like_supabase(conn):
    """Create the roles Supabase creates and hand them what Supabase hands them by default."""
    for role in EXPOSED:
        conn.execute(f"do $$ begin if not exists (select 1 from pg_roles where rolname='{role}') "
                     f"then create role {role} nologin; end if; end $$")
    # the stock grant on an original `public` schema, which a recreated schema does not carry
    conn.execute("grant usage on schema public to public")
    for role in EXPOSED:
        conn.execute(f"grant usage on schema public to {role}")
        conn.execute(f"grant select, insert, update, delete on all tables in schema public to {role}")
    # and undo 0010's policies, reproducing the state the real project was left in
    conn.execute("drop policy if exists backend_all on public.rights_grant")
    conn.execute("drop policy if exists backend_all on public.evidence_claim")


def _apply(conn, name: str):
    conn.execute((MIGRATIONS / name).read_text())


def _tables_now(conn):
    return [r["relname"] for r in conn.execute(
        "select c.relname from pg_class c join pg_namespace n on n.oid=c.relnamespace "
        "where n.nspname='public' and c.relkind='r' order by 1").fetchall()]


def test_supabase_shaped_database_is_locked_down_by_0012(conn):
    _make_it_look_like_supabase(conn)

    # the mess is real: both roles can reach the schema, and two tables lost their policy
    assert conn.execute("select has_schema_privilege('anon','public','usage') as ok").fetchone()["ok"]
    assert conn.execute("select has_table_privilege('anon','public.rights_grant','select') as ok").fetchone()["ok"]
    assert conn.execute("select count(*) as n from pg_policies where schemaname='public' and tablename='rights_grant'"
                        ).fetchone()["n"] == 0

    _apply(conn, "0012_close_exposure_gaps.sql")

    for role in EXPOSED:
        assert not conn.execute("select has_schema_privilege(%s,'public','usage') as ok", (role,)).fetchone()["ok"], \
            f"{role} still has usage on schema public"
        for t in _tables_now(conn):
            for p in PRIVS:
                got = conn.execute("select has_table_privilege(%s,%s,%s) as ok", (role, f"public.{t}", p)).fetchone()["ok"]
                assert not got, f"{role} can {p} on {t}"

    counts = {r["tablename"]: r["n"] for r in conn.execute(
        "select tablename, count(*) as n from pg_policies where schemaname='public' group by 1").fetchall()}
    assert [t for t in _tables_now(conn) if counts.get(t, 0) == 0] == []


def test_0010_alone_would_not_have_closed_these_gaps(conn):
    """Pins the regression. If someone 'simplifies' 0012 away, this fails."""
    _make_it_look_like_supabase(conn)
    _apply(conn, "0010_supabase_exposure_hardening.sql")

    still_exposed = conn.execute("select has_schema_privilege('anon','public','usage') as ok").fetchone()["ok"]
    assert still_exposed, "0010 alone was expected to leave the PUBLIC grant in place; 0012 is what removes it"

    _apply(conn, "0012_close_exposure_gaps.sql")
    assert not conn.execute("select has_schema_privilege('anon','public','usage') as ok").fetchone()["ok"]


def test_0012_refuses_to_leave_a_table_without_a_policy(conn):
    """The self-verification block must raise rather than half-apply."""
    import psycopg
    import pytest as _pytest

    _apply(conn, "0012_close_exposure_gaps.sql")
    conn.execute("create table public.orphan_check (id int)")
    conn.execute("alter table public.orphan_check enable row level security")
    conn.execute("drop policy if exists backend_all on public.orphan_check")
    # simulate the role vanishing so the policy loop cannot run, then confirm the assertion fires
    with _pytest.raises(psycopg.errors.RaiseException, match="row-level security is on with no policy"):
        with conn.transaction():
            conn.execute("""
                do $$
                declare missing text[];
                begin
                  select array_agg(c.relname order by c.relname) into missing
                    from pg_class c join pg_namespace n on n.oid = c.relnamespace
                   where n.nspname='public' and c.relkind='r' and c.relrowsecurity
                     and not exists (select 1 from pg_policies p where p.schemaname='public' and p.tablename=c.relname);
                  if missing is not null then
                    raise exception 'row-level security is on with no policy for: %', array_to_string(missing, ', ');
                  end if;
                end $$;
            """)
