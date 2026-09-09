"""MoveAI ingestion API. Mounted at /v1; the VPS reverse proxy exposes it as /api/v1 (spec §22B)."""

from __future__ import annotations

import os
from typing import Any

import psycopg
from fastapi import Depends, FastAPI

from .auth import Principal, as_dict, current_principal
from .db import get_conn
from .errors import install
from .middleware import RequestContext
from .routers import campaigns, catalog, plans, sources

app = FastAPI(
    title="MoveAI Exercise Ingestion and Plan-Drafting Engine",
    version="0.1.0",
    docs_url="/v1/docs",
    openapi_url="/v1/openapi.json",
    root_path=os.environ.get("API_ROOT_PATH", ""),
)
app.add_middleware(RequestContext)
install(app)
for r in (sources.router, catalog.router, plans.router, campaigns.router):
    app.include_router(r, prefix="/v1")


@app.get("/v1/healthz", tags=["ops"])
def healthz() -> dict[str, str]:
    """Minimal public health output; detailed diagnostics are authenticated (spec §22D)."""
    return {"status": "ok"}


@app.get("/v1/me", tags=["ops"])
def me(p: Principal = Depends(current_principal)) -> dict[str, Any]:
    return as_dict(p)


@app.get("/v1/diagnostics", tags=["ops"])
def diagnostics(p: Principal = Depends(current_principal), conn: psycopg.Connection = Depends(get_conn)) -> dict[str, Any]:
    q = {r["state"]: r["n"] for r in conn.execute("select state, count(*) as n from ingestion_job group by state").fetchall()}
    ob = conn.execute(
        "select count(*) filter (where delivered_at is null) as undelivered, count(*) filter (where dead_lettered) as dead from outbox_event"
    ).fetchone()
    return {
        "queue": q,
        "outbox": ob,
        "release": (conn.execute("select label, published_at from catalog_release order by published_at desc limit 1").fetchone()),
        "migrations": [r["name"] for r in conn.execute("select name from schema_migration order by name").fetchall()],
    }


@app.get("/v1/users", tags=["ops"])
def users(p: Principal = Depends(current_principal), conn: psycopg.Connection = Depends(get_conn)) -> dict[str, Any]:
    """Dev role switcher support: lists users in the caller's tenant. Replaced by Supabase Auth user management."""
    rows = conn.execute(
        "select id, email, display_name, roles::text[] as roles, tenant_id from app_user where tenant_id=%s or %s is null order by display_name",
        (p.tenant_id, p.tenant_id),
    ).fetchall()
    return {
        "items": [
            {
                "id": str(r["id"]),
                "email": r["email"],
                "display_name": r["display_name"],
                "roles": r["roles"],
                "tenant_id": str(r["tenant_id"]) if r["tenant_id"] else None,
            }
            for r in rows
        ]
    }
