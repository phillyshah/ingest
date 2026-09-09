"""Root pytest fixtures: a migrated local Postgres, a per-test rolled-back connection, seeded tenants/users."""

from __future__ import annotations

import os
import subprocess
import uuid
from collections.abc import Iterator
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent


def pytest_configure(config: pytest.Config) -> None:
    os.environ.setdefault("DATABASE_URL", "postgresql://postgres@127.0.0.1:55432/moveai")
    os.environ.setdefault("MOVEAI_ENV", "test")
    os.environ.setdefault("PLAN_SIGNING_SECRET", "test-secret-not-for-production")


def _test_url() -> str:
    """Tests never run against the development database: derive `<db>_test` (or TEST_DATABASE_URL) and reset it."""
    if os.environ.get("TEST_DATABASE_URL"):
        return os.environ["TEST_DATABASE_URL"]
    base = os.environ["DATABASE_URL"]
    head, _, db = base.rpartition("/")
    name, _, query = db.partition("?")
    return f"{head}/{name}_test" + (f"?{query}" if query else "")


@pytest.fixture(scope="session")
def migrated_db() -> str:
    import psycopg

    url = _test_url()
    head, _, db = url.rpartition("/")
    name = db.partition("?")[0]
    with psycopg.connect(f"{head}/postgres", autocommit=True) as admin:
        if not admin.execute("select 1 from pg_database where datname=%s", (name,)).fetchone():
            admin.execute(f'create database "{name}"')
    env = {**os.environ, "DATABASE_URL": url}
    subprocess.run(["python", str(ROOT / "scripts" / "migrate.py"), "--reset"], check=True, env=env, capture_output=True)
    os.environ["DATABASE_URL"] = url
    return url


@pytest.fixture()
def conn(migrated_db: str) -> Iterator:
    from moveai_db import connect

    c = connect(migrated_db)
    try:
        yield c
    finally:
        c.rollback()
        c.close()


@pytest.fixture()
def tenants(conn) -> dict[str, uuid.UUID]:
    out = {}
    for name in ("tenant_a", "tenant_b"):
        row = conn.execute("insert into tenant(name) values (%s) returning id", (f"{name}-{uuid.uuid4().hex[:6]}",)).fetchone()
        out[name] = row["id"]
    return out


@pytest.fixture()
def users(conn, tenants) -> dict[str, uuid.UUID]:
    out = {}
    for key, roles in {
        "admin": "{source_admin}",
        "rights": "{rights_reviewer}",
        "pt": "{pt}",
        "pt2": "{pt}",
        "lead": "{clinical_lead}",
        "auditor": "{auditor}",
        "integration": "{integration}",
    }.items():
        row = conn.execute(
            "insert into app_user(tenant_id,email,display_name,roles) values (%s,%s,%s,%s) returning id",
            (tenants["tenant_a"], f"{key}-{uuid.uuid4().hex[:6]}@example.test", key, roles),
        ).fetchone()
        out[key] = row["id"]
    return out
