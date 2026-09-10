"""Apply db/migrations/*.sql in order, recording each in schema_migration. Additive only.

Safety: the target database is always printed before anything runs, and `--reset` (which drops the public schema)
refuses to act unless ALLOW_DESTRUCTIVE_RESET=1 is set. Never use --reset against a real project.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path
from urllib.parse import urlparse

import psycopg

ROOT = Path(__file__).resolve().parents[1]
MIGRATIONS = ROOT / "db" / "migrations"


def database_url() -> str:
    url = os.environ.get("DATABASE_URL")
    if not url:
        sys.exit("DATABASE_URL is not set (run `make db-up`)")
    return url


def describe(url: str) -> str:
    """Host, port and database only. Never prints the password."""
    p = urlparse(url)
    return f"{p.hostname or '?'}:{p.port or 5432}/{(p.path or '/').lstrip('/') or '?'} as {p.username or '?'}"


def looks_managed(url: str) -> bool:
    host = (urlparse(url).hostname or "").lower()
    return any(m in host for m in ("supabase.co", "supabase.com", "pooler.supabase.com", "rds.amazonaws.com", "neon.tech"))


def reset(conn: psycopg.Connection) -> None:
    conn.execute("drop schema public cascade; create schema public;")
    conn.execute(
        "do $$ begin if exists (select 1 from pg_roles where rolname='moveai_app') then "
        "revoke all on schema public from moveai_app; end if; end $$;"
    )
    conn.commit()


def migrate(conn: psycopg.Connection) -> list[str]:
    conn.execute("create table if not exists schema_migration (name text primary key, applied_at timestamptz not null default now())")
    applied = {r[0] for r in conn.execute("select name from schema_migration")}
    done: list[str] = []
    for path in sorted(MIGRATIONS.glob("*.sql")):
        if path.name in applied:
            continue
        with conn.transaction():
            conn.execute(path.read_text())
            conn.execute("insert into schema_migration(name) values (%s)", (path.name,))
        done.append(path.name)
    conn.commit()
    return done


if __name__ == "__main__":
    url = database_url()
    print(f"target: {describe(url)}")
    if "--reset" in sys.argv:
        if os.environ.get("ALLOW_DESTRUCTIVE_RESET") != "1":
            sys.exit("refusing --reset: it drops the public schema. Set ALLOW_DESTRUCTIVE_RESET=1 for a scratch database.")
        if looks_managed(url):
            sys.exit(f"refusing --reset against a managed database ({describe(url)}). Drop it from the provider console instead.")
    with psycopg.connect(url, autocommit=False) as c:
        if "--reset" in sys.argv:
            print("resetting schema public (destructive)")
            reset(c)
        applied = migrate(c)
        for name in applied:
            print("applied", name)
        print(f"schema up to date ({len(applied)} applied this run)")
