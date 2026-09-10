"""Authentication and authorization boundary.

Two modes behind one `Principal` type, so routers never change:

* ``AUTH_MODE=shim``     - development only. Identity comes from ``X-User-Id`` / ``X-Tenant-Id`` / ``X-Role``.
* ``AUTH_MODE=supabase`` - production. A Supabase Auth JWT is verified, then mapped to an ``app_user`` row.

Access is invitation-only (spec §22C): a cryptographically valid token whose subject has no ``app_user`` row is
refused. Signing up in Supabase Auth grants nothing on its own. Permissions are enforced here *and* in the
database (row-level security, author-cannot-approve checks), never in the UI alone.
"""

from __future__ import annotations

import os
import time
from dataclasses import dataclass, field
from typing import Any

import psycopg
from fastapi import Depends, HTTPException, Request
from moveai_contracts.enums import UserRole

from .db import get_conn

USER_COLUMNS = "id, tenant_id, email, display_name, roles::text[] as roles, auth_user_id"


@dataclass
class Principal:
    user_id: str
    tenant_id: str | None
    roles: list[str] = field(default_factory=list)
    display_name: str | None = None
    kind: str = "user"

    def has(self, *roles: str) -> bool:
        return bool(set(roles) & set(self.roles))


def _unauthenticated(code: str, message: str) -> HTTPException:
    return HTTPException(401, {"code": code, "message": message})


def _principal_from_row(row: dict[str, Any], requested_role: str | None, requested_tenant: str | None) -> Principal:
    """Shared by both modes: narrow to one role if asked, and refuse a tenant the user does not belong to."""
    roles = list(row["roles"])
    if requested_role:
        if requested_role not in roles:
            raise HTTPException(403, {"code": "role_not_held", "message": f"user does not hold role {requested_role}"})
        roles = [requested_role]
    tenant = requested_tenant or (str(row["tenant_id"]) if row["tenant_id"] else None)
    if tenant and row["tenant_id"] and tenant != str(row["tenant_id"]):
        raise HTTPException(403, {"code": "tenant_mismatch", "message": "user does not belong to that tenant"})
    return Principal(user_id=str(row["id"]), tenant_id=tenant, roles=roles, display_name=row["display_name"])


# ---------------------------------------------------------------- development shim
def _shim(request: Request, conn: psycopg.Connection) -> Principal:
    user_id = request.headers.get("x-user-id")
    if not user_id:
        raise _unauthenticated("unauthenticated", "missing X-User-Id (dev shim) or bearer token")
    try:
        row = conn.execute(f"select {USER_COLUMNS} from app_user where id=%s", (user_id,)).fetchone()
    except psycopg.errors.InvalidTextRepresentation as e:
        raise _unauthenticated("unknown_user", "user not found") from e
    if not row:
        raise _unauthenticated("unknown_user", "user not found")
    return _principal_from_row(row, request.headers.get("x-role"), request.headers.get("x-tenant-id"))


# ---------------------------------------------------------------- Supabase Auth
_JWKS_CLIENT: Any = None
_JWKS_URL: str | None = None


def _issuer() -> str:
    url = os.environ.get("SUPABASE_URL")
    if not url:
        raise HTTPException(500, {"code": "auth_misconfigured", "message": "SUPABASE_URL is not set"})
    return url.rstrip("/") + "/auth/v1"


def _jwks_client() -> Any:
    """Cached JWKS client. Supabase signs with asymmetric keys by default and publishes them here."""
    global _JWKS_CLIENT, _JWKS_URL
    import jwt

    url = _issuer() + "/.well-known/jwks.json"
    if _JWKS_CLIENT is None or _JWKS_URL != url:
        _JWKS_CLIENT = jwt.PyJWKClient(url, cache_keys=True, lifespan=int(os.environ.get("JWKS_CACHE_SECONDS", "300")))
        _JWKS_URL = url
    return _JWKS_CLIENT


def verify_token(token: str) -> dict[str, Any]:
    """Verify signature, expiry, audience and issuer. Raises HTTPException(401) on any failure."""
    import jwt

    issuer = _issuer()
    audience = os.environ.get("SUPABASE_JWT_AUDIENCE", "authenticated")
    options = {"require": ["exp", "sub"]}
    secret = os.environ.get("SUPABASE_JWT_SECRET")
    try:
        if secret:  # legacy shared-secret projects
            return jwt.decode(token, secret, algorithms=["HS256"], audience=audience, issuer=issuer, options=options)
        key = _jwks_client().get_signing_key_from_jwt(token).key
        return jwt.decode(token, key, algorithms=["ES256", "RS256"], audience=audience, issuer=issuer, options=options)
    except jwt.ExpiredSignatureError as e:
        raise _unauthenticated("token_expired", "the session has expired; sign in again") from e
    except jwt.InvalidAudienceError as e:
        raise _unauthenticated("bad_audience", "token audience does not match this deployment") from e
    except jwt.InvalidIssuerError as e:
        raise _unauthenticated("bad_issuer", "token was not issued by the configured Supabase project") from e
    except jwt.InvalidTokenError as e:
        raise _unauthenticated("invalid_token", f"token verification failed: {e}") from e
    except Exception as e:  # JWKS fetch failure and similar: never fall through to an unauthenticated request
        raise _unauthenticated("verification_unavailable", f"could not verify the token: {type(e).__name__}") from e


def _supabase(request: Request, conn: psycopg.Connection) -> Principal:
    header = request.headers.get("authorization", "")
    if not header.lower().startswith("bearer "):
        raise _unauthenticated("unauthenticated", "missing bearer token")
    claims = verify_token(header.split(" ", 1)[1].strip())
    subject = claims.get("sub")
    row = conn.execute(f"select {USER_COLUMNS} from app_user where auth_user_id=%s", (subject,)).fetchone()
    if not row:
        # Invitation-only: a valid Supabase identity is not an authorization to use this service.
        raise HTTPException(403, {"code": "not_invited", "message": "this account has no access; an administrator must invite it"})
    if not row["roles"]:
        raise HTTPException(403, {"code": "no_roles", "message": "this account holds no roles"})
    return _principal_from_row(row, request.headers.get("x-role"), request.headers.get("x-tenant-id"))


# ---------------------------------------------------------------- dependencies
def current_principal(request: Request, conn: psycopg.Connection = Depends(get_conn)) -> Principal:
    mode = os.environ.get("AUTH_MODE", "shim")
    if mode == "shim" and os.environ.get("MOVEAI_ENV") == "production":
        raise HTTPException(500, {"code": "auth_misconfigured", "message": "the development auth shim cannot be used in production"})
    p = _shim(request, conn) if mode == "shim" else _supabase(request, conn)
    # Bind the tenant for row-level security for the rest of this connection's work.
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


def _unused_time() -> float:  # keeps `time` import meaningful for JWKS lifespan tuning
    return time.time()
