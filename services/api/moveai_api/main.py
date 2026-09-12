"""MoveAI ingestion API. Mounted at /v1; the VPS reverse proxy exposes it as /api/v1 (spec §22B)."""

from __future__ import annotations

import os
from typing import Any

import psycopg
from fastapi import Depends, FastAPI, HTTPException

from .auth import Principal, as_dict, current_principal
from .db import get_conn
from .errors import install
from .middleware import RequestContext
from .routers import campaigns, catalog, plans, source_policies, sources

app = FastAPI(
    title="MoveAI Exercise Ingestion and Plan-Drafting Engine",
    version="0.1.0",
    docs_url="/v1/docs",
    openapi_url="/v1/openapi.json",
    root_path=os.environ.get("API_ROOT_PATH", ""),
)
app.add_middleware(RequestContext)
install(app)
for r in (sources.router, source_policies.router, catalog.router, plans.router, campaigns.router):
    app.include_router(r, prefix="/v1")


@app.get("/v1/healthz", tags=["ops"])
def healthz() -> dict[str, str]:
    """Minimal public health output; detailed diagnostics are authenticated (spec §22D)."""
    return {"status": "ok"}


@app.get("/v1/version", tags=["ops"])
def version() -> dict[str, Any]:
    """Which build is actually running.

    Unauthenticated and deliberately thin: a commit SHA and a build time, nothing about the machine. After a
    deploy the only way to be sure the new code is live is to ask the running process, not the repository.
    """
    return {
        "version": app.version,
        "commit": os.environ.get("GIT_SHA", "unknown"),
        "built_at": os.environ.get("BUILT_AT", "unknown"),
        "environment": os.environ.get("MOVEAI_ENV", "unknown"),
    }


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


@app.post("/v1/dev-login", tags=["ops"])
def dev_login(body: dict[str, Any], conn: psycopg.Connection = Depends(get_conn)) -> dict[str, Any]:
    """Resolve a username to a development identity, so nobody has to paste a UUID to sign in.

    Unauthenticated by necessity — it is what produces the identity — and therefore refused outright unless the
    deployment is explicitly running the development shim. In production, Supabase Auth issues identities and
    this route is not a way in.

    It creates the named user when absent, with every role, because on a staging deployment whose whole purpose
    is to look at content there is nothing to protect by refusing. That is also exactly why it must never be
    reachable in production, and why the two guards below are separate: AUTH_MODE could plausibly be
    misconfigured on its own, MOVEAI_ENV is set deliberately.
    """
    if os.environ.get("AUTH_MODE", "shim") != "shim" or os.environ.get("MOVEAI_ENV") == "production":
        raise HTTPException(404, {"code": "not_found", "message": "not available"})

    name = str(body.get("username", "")).strip()
    if not name:
        raise HTTPException(400, {"code": "username_required", "message": "enter a username"})

    row = conn.execute(
        "select id, tenant_id, display_name, roles::text[] as roles from app_user "
        "where lower(display_name)=lower(%s) or lower(split_part(email,'@',1))=lower(%s) limit 1",
        (name, name),
    ).fetchone()

    if not row:
        tenant = conn.execute("select id from tenant order by created_at limit 1").fetchone()
        if not tenant:
            raise HTTPException(409, {"code": "not_seeded", "message": "this database has no tenant yet; run the seed first"})
        all_roles = [r["v"] for r in conn.execute("select unnest(enum_range(null::user_role))::text as v").fetchall()]
        row = conn.execute(
            "insert into app_user(tenant_id, email, display_name, roles) values (%s,%s,%s,%s) "
            "returning id, tenant_id, display_name, roles::text[] as roles",
            (tenant["id"], f"{name.lower()}@local.invalid", name, all_roles),
        ).fetchone()
        # No commit here: get_conn() commits the request's transaction on success. Committing inside a handler
        # ends that transaction early, which also breaks the per-test rollback the suite relies on.

    roles = list(row["roles"])
    return {
        "user_id": str(row["id"]),
        "tenant_id": str(row["tenant_id"]) if row["tenant_id"] else None,
        "display_name": row["display_name"],
        "roles": roles,
        "role": "source_admin" if "source_admin" in roles else (roles[0] if roles else None),
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
