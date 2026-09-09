"""Thin psycopg 3 helpers: connection factory, tenant context, JSON adaptation, dict rows."""
from __future__ import annotations

import json
import os
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import date, datetime
from decimal import Decimal
from typing import Any

import psycopg
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb, set_json_dumps


def _default(o: Any) -> Any:
    if isinstance(o, uuid.UUID):
        return str(o)
    if isinstance(o, datetime | date):
        return o.isoformat()
    if isinstance(o, Decimal):
        return float(o)
    if hasattr(o, "model_dump"):
        return o.model_dump(mode="json")
    raise TypeError(f"not JSON serializable: {type(o)!r}")


set_json_dumps(lambda obj: json.dumps(obj, default=_default))


def J(value: Any) -> Jsonb:
    """Wrap a python value for a jsonb parameter."""
    return Jsonb(value)


def database_url() -> str:
    url = os.environ.get("DATABASE_URL")
    if not url:
        raise RuntimeError("DATABASE_URL is not set")
    return url


def connect(url: str | None = None, *, autocommit: bool = False) -> psycopg.Connection[dict[str, Any]]:
    return psycopg.connect(url or database_url(), row_factory=dict_row, autocommit=autocommit)


@contextmanager
def tenant_context(conn: psycopg.Connection[Any], tenant_id: uuid.UUID | str | None) -> Iterator[None]:
    """Set app.tenant_id for RLS for the duration of the block (transaction-local)."""
    conn.execute("select set_config('app.tenant_id', %s, true)", (str(tenant_id) if tenant_id else "",))
    try:
        yield
    finally:
        conn.execute("select set_config('app.tenant_id', '', true)")


def set_tenant(conn: psycopg.Connection[Any], tenant_id: uuid.UUID | str | None) -> None:
    conn.execute("select set_config('app.tenant_id', %s, false)", (str(tenant_id) if tenant_id else "",))


def one(conn: psycopg.Connection[Any], sql: str, params: Any = None) -> dict[str, Any] | None:
    return conn.execute(sql, params).fetchone()


def all_rows(conn: psycopg.Connection[Any], sql: str, params: Any = None) -> list[dict[str, Any]]:
    return conn.execute(sql, params).fetchall()
