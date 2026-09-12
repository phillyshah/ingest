"""Refuse to deploy code whose migrations have not been applied.

This exists because the failure already happened. Migration 0013 added a column, the application was deployed
without running the migration, and every query touching that column began returning a 500 — which the browser
surfaced as an unrelated-looking JSON parse error. The database and the code are two separately deployed things,
and nothing was comparing them.

Run before restarting the application. A database that is behind the code is fatal: those queries cannot work.
A database that is ahead is fine and common — migrations are additive, so an older build keeps running against a
newer schema, which is exactly what makes a rollback safe.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path
from urllib.parse import urlparse

import psycopg

ROOT = Path(__file__).resolve().parents[1]
MIGRATIONS = ROOT / "db" / "migrations"


def main() -> int:
    url = os.environ.get("DATABASE_URL")
    if not url:
        print("DATABASE_URL is not set; cannot check the schema.", file=sys.stderr)
        return 2

    p = urlparse(url)
    print(f"schema check against {p.hostname or '?'}:{p.port or 5432}/{(p.path or '/').lstrip('/') or '?'}")

    on_disk = sorted(f.name for f in MIGRATIONS.glob("*.sql"))
    with psycopg.connect(url) as conn:
        exists = conn.execute("select to_regclass('public.schema_migration') as t").fetchone()[0]
        if not exists:
            applied: set[str] = set()
        else:
            applied = {r[0] for r in conn.execute("select name from schema_migration")}

    missing = [m for m in on_disk if m not in applied]
    ahead = sorted(applied - set(on_disk))

    if ahead:
        # Not an error: the database is newer than this checkout, which is what a rollback looks like.
        print(f"  note: the database has {len(ahead)} migration(s) this build does not know about: {', '.join(ahead)}")

    if not missing:
        print(f"  up to date: all {len(on_disk)} migrations applied.")
        return 0

    print("", file=sys.stderr)
    print(f"REFUSING TO DEPLOY: the database is missing {len(missing)} migration(s) this build needs:", file=sys.stderr)
    for m in missing:
        print(f"  - {m}", file=sys.stderr)
    print("", file=sys.stderr)
    print("Apply them first, then deploy again:", file=sys.stderr)
    print("  GitHub -> Actions -> Migrate Supabase -> Run workflow (type 'migrate', leave the seed box unticked)", file=sys.stderr)
    print("", file=sys.stderr)
    print("Nothing was restarted. The version currently running is untouched.", file=sys.stderr)
    return 1


if __name__ == "__main__":
    sys.exit(main())
