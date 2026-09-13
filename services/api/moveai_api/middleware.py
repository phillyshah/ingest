"""Request IDs, audit events, idempotency keys for mutating client requests (spec §10)."""

from __future__ import annotations

import hashlib
import json
import uuid

from moveai_db import J, pooled
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse, Response


class RequestContext(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        request.state.request_id = request.headers.get("x-request-id") or uuid.uuid4().hex
        body = b""
        if request.method in ("POST", "PATCH", "PUT", "DELETE"):
            body = await request.body()
        idem = request.headers.get("idempotency-key")
        user = request.headers.get("x-user-id")
        tenant = request.headers.get("x-tenant-id")
        if idem and request.method in ("POST", "PATCH"):
            h = hashlib.sha256(body).hexdigest()
            with pooled() as c:
                row = c.execute(
                    "select * from idempotency_key where key=%s and coalesce(tenant_id::text,'')=%s and coalesce(user_id::text,'')=%s",
                    (idem, tenant or "", user or ""),
                ).fetchone()
            if row:
                if row["request_hash"] != h:
                    return JSONResponse(
                        status_code=409,
                        content={"code": "idempotency_conflict", "message": "same key, different payload"},
                    )
                return JSONResponse(
                    status_code=row["status_code"],
                    content=row["response"],
                    headers={"idempotent-replay": "true"},
                )
        response: Response = await call_next(request)
        response.headers["x-request-id"] = request.state.request_id
        if idem and request.method in ("POST", "PATCH") and response.status_code < 500:
            chunks = [c async for c in response.body_iterator]  # type: ignore[attr-defined]
            raw = b"".join(chunks)
            try:
                payload = json.loads(raw or b"null")
            except ValueError:
                payload = None
            with pooled() as c:
                c.execute(
                    """insert into idempotency_key(key, tenant_id, user_id, request_hash, status_code, response) values (%s,%s,%s,%s,%s,%s)
                             on conflict do nothing""",
                    (
                        idem,
                        tenant or None,
                        user or None,
                        hashlib.sha256(body).hexdigest(),
                        response.status_code,
                        J(payload),
                    ),
                )
                c.commit()
            response = Response(
                content=raw,
                status_code=response.status_code,
                headers=dict(response.headers),
                media_type=response.media_type,
            )
        if request.method in ("POST", "PATCH", "PUT", "DELETE") and not request.url.path.endswith("/healthz"):
            p = getattr(request.state, "principal", None)
            with pooled() as c:
                c.execute(
                    """insert into audit_event(tenant_id, actor_id, actor_kind, action, request_id, detail) values (%s,%s,%s,%s,%s,%s)""",
                    (
                        p.tenant_id if p else None,
                        p.user_id if p else None,
                        "user" if p else "service",
                        f"{request.method} {request.url.path}",
                        request.state.request_id,
                        J({"status": response.status_code}),
                    ),
                )
                c.commit()
        return response
