"""Apply db/migrations/*.sql in order, recording each in schema_migration. Additive only."""

from __future__ import annotations

import os
import sys
from pathlib import Path

import psycopg

ROOT = Path(__file__).resolve().parents[1]
MIGRATIONS = ROOT / "db" / "migrations"


def database_url() -> str:
    url = os.environ.get("DATABASE_URL")
    if not url:
        sys.exit("DATABASE_URL is not set (run `make db-up`)")
    return url


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
    with psycopg.connect(database_url(), autocommit=False) as c:
        if "--reset" in sys.argv:
            reset(c)
        for name in migrate(c):
            print("applied", name)
        print("schema up to date")
