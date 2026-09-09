"""Authentication/authorization boundary. Development: header shim. Production: Supabase Auth JWT verification
plugs in behind `Principal` (same interface). Permissions are also enforced in the database (RLS, checks)."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Any

import psycopg
from fastapi import Depends, HTTPException, Request
from moveai_contracts.enums import UserRole

from .db import get_conn


@dataclass
class Principal:
    user_id: str
    tenant_id: str | None
    roles: list[str] = field(default_factory=list)
    display_name: str | None = None
    kind: str = "user"

    def has(self, *roles: str) -> bool:
        return bool(set(roles) & set(self.roles))


def _shim(request: Request, conn: psycopg.Connection) -> Principal:
    user_id = request.headers.get("x-user-id")
    if not user_id:
        raise HTTPException(401, {"code": "unauthenticated", "message": "missing X-User-Id (dev shim) or bearer token"})
    row = conn.execute(
        "select id, tenant_id, email, display_name, roles::text[] as roles from app_user where id=%s",
        (user_id,),
    ).fetchone()
    if not row:
        raise HTTPException(401, {"code": "unknown_user", "message": "user not found"})
    roles = [r for r in row["roles"]]
    requested = request.headers.get("x-role")
    if requested:
        if requested not in roles:
            raise HTTPException(403, {"code": "role_not_held", "message": f"user does not hold role {requested}"})
        roles = [requested]
    tenant = request.headers.get("x-tenant-id") or (str(row["tenant_id"]) if row["tenant_id"] else None)
    if tenant and row["tenant_id"] and tenant != str(row["tenant_id"]):
        raise HTTPException(403, {"code": "tenant_mismatch", "message": "user does not belong to that tenant"})
    return Principal(user_id=str(row["id"]), tenant_id=tenant, roles=roles, display_name=row["display_name"])


def _supabase(request: Request, conn: psycopg.Connection) -> Principal:  # pragma: no cover - wired at deployment
    raise HTTPException(
        501,
        {
            "code": "auth_not_configured",
            "message": "Supabase Auth verification is not configured; set AUTH_MODE=shim for development",
        },
    )


def current_principal(request: Request, conn: psycopg.Connection = Depends(get_conn)) -> Principal:
    mode = os.environ.get("AUTH_MODE", "shim")
    p = _shim(request, conn) if mode == "shim" else _supabase(request, conn)
    conn.execute("select set_config('app.tenant_id', %s, false)", (p.tenant_id or "",))
    request.state.principal = p
    return p


def require(*roles: str):
    def dep(p: Principal = Depends(current_principal)) -> Principal:
        if roles and not p.has(*roles):
            raise HTTPException(403, {"code": "forbidden", "message": f"requires one of {list(roles)}"})
        return p

    return dep


ROLE_VALUES = [r.value for r in UserRole]


def as_dict(p: Principal) -> dict[str, Any]:
    return {"user_id": p.user_id, "tenant_id": p.tenant_id, "roles": p.roles, "display_name": p.display_name}
