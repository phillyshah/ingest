"""Prove the Supabase exposure boundary on a real project (spec §22C, migration 0010).

Checks, against whatever DATABASE_URL points at:
  1. `anon` and `authenticated` hold no privilege on any engine table, and no usage on schema public.
  2. Row-level security is enabled on every engine table.
  3. Every table has at least one policy, so nothing is accidentally wide open.
  4. Reports objects in Supabase-managed schemas so you can confirm they were not touched.

Exits non-zero if anything is exposed. Read-only: it changes nothing.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import psycopg
from psycopg.rows import dict_row

sys.path.insert(0, str(Path(__file__).resolve().parent))
from migrate import describe  # noqa: E402

EXPOSED_ROLES = ("anon", "authenticated")
PRIVILEGES = ("select", "insert", "update", "delete")
MANAGED_SCHEMAS = ("auth", "storage", "realtime", "vault", "graphql", "extensions")


def main() -> int:
    url = os.environ.get("DATABASE_URL")
    if not url:
        sys.exit("DATABASE_URL is not set")
    problems: list[str] = []
    notes: list[str] = []
    with psycopg.connect(url, row_factory=dict_row) as conn:
        print(f"target: {describe(url)}\n")
        roles = {r["rolname"] for r in conn.execute("select rolname from pg_roles").fetchall()}
        present = [r for r in EXPOSED_ROLES if r in roles]
        print(f"roles present: {', '.join(sorted(roles & {*EXPOSED_ROLES, 'moveai_app', 'postgres', 'service_role'})) or 'none'}")
        if not present:
            notes.append(
                "neither anon nor authenticated exists here, so PostgREST exposure does not apply "
                "(expected on local development PostgreSQL, not on Supabase)"
            )

        tables = [
            r["relname"]
            for r in conn.execute(
                "select c.relname from pg_class c join pg_namespace n on n.oid=c.relnamespace "
                "where n.nspname='public' and c.relkind='r' order by c.relname"
            ).fetchall()
        ]
        print(f"engine tables in public: {len(tables)}")

        # 1. privileges
        for role in present:
            if conn.execute("select has_schema_privilege(%s,'public','usage') as ok", (role,)).fetchone()["ok"]:
                problems.append(f"{role} still has USAGE on schema public")
            exposed = []
            for t in tables:
                granted = [
                    p
                    for p in PRIVILEGES
                    if conn.execute("select has_table_privilege(%s, %s, %s) as ok", (role, f"public.{t}", p)).fetchone()["ok"]
                ]
                if granted:
                    exposed.append(f"{t} ({', '.join(granted)})")
            if exposed:
                problems.append(
                    f"{role} can still reach {len(exposed)} table(s): {', '.join(exposed[:8])}" + (" ..." if len(exposed) > 8 else "")
                )
            else:
                print(f"  {role}: no privilege on any engine table")

        # 2/3. row-level security and policies
        no_rls = [
            r["relname"]
            for r in conn.execute(
                "select c.relname from pg_class c join pg_namespace n on n.oid=c.relnamespace "
                "where n.nspname='public' and c.relkind='r' and c.relrowsecurity=false order by 1"
            ).fetchall()
        ]
        if no_rls:
            problems.append(f"row-level security is OFF on {len(no_rls)} table(s): {', '.join(no_rls)}")
        else:
            print(f"  row-level security: enabled on all {len(tables)} tables")
        counts = {
            r["tablename"]: r["n"]
            for r in conn.execute("select tablename, count(*) as n from pg_policies where schemaname='public' group by 1").fetchall()
        }
        missing = [t for t in tables if counts.get(t, 0) == 0]
        if missing:
            problems.append(f"no policy at all on: {', '.join(missing)}")
        else:
            print(f"  policies: {sum(counts.values())} across {len(counts)} tables")

        # 4. managed schemas, informational
        for s in MANAGED_SCHEMAS:
            row = conn.execute(
                "select count(*) as n from pg_class c join pg_namespace n on n.oid=c.relnamespace where n.nspname=%s and c.relkind='r'",
                (s,),
            ).fetchone()
            if row["n"]:
                notes.append(f"schema {s}: {row['n']} table(s) present and untouched by this engine")

    print()
    for n in notes:
        print(f"note: {n}")
    if problems:
        print("\nFAILED — the schema is exposed:")
        for p in problems:
            print(f"  - {p}")
        return 1
    print("\nPASSED — no anon/authenticated exposure, RLS on every engine table.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
