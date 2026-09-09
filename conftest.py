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


@pytest.fixture(scope="session")
def migrated_db() -> str:
    subprocess.run(["python", str(ROOT / "scripts" / "migrate.py")], check=True, env=os.environ, capture_output=True)
    return os.environ["DATABASE_URL"]


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
    for key, roles in {"admin": "{source_admin}", "rights": "{rights_reviewer}", "pt": "{pt}", "pt2": "{pt}",
                       "lead": "{clinical_lead}", "auditor": "{auditor}", "integration": "{integration}"}.items():
        row = conn.execute(
            "insert into app_user(tenant_id,email,display_name,roles) values (%s,%s,%s,%s) returning id",
            (tenants["tenant_a"], f"{key}-{uuid.uuid4().hex[:6]}@example.test", key, roles)).fetchone()
        out[key] = row["id"]
    return out
