"""Seed a database for development, tests, and the demo: terminology releases, content packs, a catalog release."""
from __future__ import annotations

import uuid
from pathlib import Path
from typing import Any

import psycopg

from moveai_ingestion.config import FIXTURES
from moveai_ingestion.packs import install_pack
from moveai_ingestion.terminology import import_releases
from moveai_rules.pack import load_all

from .release import publish


def ensure_users(conn: psycopg.Connection, tenant_name: str = "demo-tenant") -> dict[str, Any]:
    t = conn.execute("insert into tenant(name) values (%s) on conflict (name) do update set name=excluded.name returning id", (tenant_name,)).fetchone()["id"]
    users = {}
    for key, roles, email in (("admin", "{source_admin}", "admin@demo.test"), ("rights", "{rights_reviewer}", "rights@demo.test"),
                              ("pt", "{pt}", "pt@demo.test"), ("pt2", "{pt}", "pt2@demo.test"), ("lead", "{clinical_lead}", "lead@demo.test"),
                              ("auditor", "{auditor}", "auditor@demo.test"), ("integration", "{integration}", "moveai-adapter@demo.test")):
        users[key] = conn.execute(
            "insert into app_user(tenant_id,email,display_name,roles) values (%s,%s,%s,%s) on conflict (email) do update set roles=excluded.roles returning id",
            (t, email, key, roles)).fetchone()["id"]
    return {"tenant_id": t, **users}


def seed(conn: psycopg.Connection, *, packs_dir: Path | None = None, publish_release: bool = True) -> dict[str, Any]:
    ctx = ensure_users(conn)
    import_releases(conn, FIXTURES / "terminology")
    packs = load_all(packs_dir or FIXTURES / "content-packs")
    installed = []
    for p in packs:
        installed.append(install_pack(conn, p, author_id=ctx["pt"], approver_id=ctx["lead"] if p.signed else None))
    release = publish(conn, label=f"seed-{uuid.uuid4().hex[:6]}", published_by=ctx["lead"]) if publish_release else None
    return {**ctx, "packs": installed, "release": release}
