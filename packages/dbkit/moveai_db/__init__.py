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
    """A fresh, dedicated connection. For the worker, scripts and tests — long-lived processes that hold one.

    A request-serving process must not call this per request: see `pool()`.
    """
    return psycopg.connect(url or database_url(), row_factory=dict_row, autocommit=autocommit)


_POOL = None


def pool():
    """The process-wide connection pool, created on first use.

    Why a pool and not a connection per request: the deployed database sits behind Supabase's session pooler,
    which admits 15 clients in total. The API used to open a new connection for every request, another for the
    audit record, and one per open board tab for the live stream — with the worker and scheduler holding theirs,
    a couple of browser tabs polling was enough to hit the ceiling and turn every request into a 500. The pool
    caps what this process can hold (`DB_POOL_MAX`, default 6) and reuses connections instead of reopening them.
    """
    global _POOL
    if _POOL is None:
        from psycopg_pool import ConnectionPool

        _POOL = ConnectionPool(
            database_url(),
            min_size=0,
            max_size=int(os.environ.get("DB_POOL_MAX", "6")),
            timeout=float(os.environ.get("DB_POOL_WAIT_S", "15")),  # how long a request waits for a free connection
            max_idle=120,  # let idle connections go, so the pooler's slots are free for the worker and scripts
            kwargs={"row_factory": dict_row},
            open=True,
        )
    return _POOL


@contextmanager
def pooled() -> Iterator[psycopg.Connection[dict[str, Any]]]:
    """A pooled connection for the duration of the block; committed on a clean exit, rolled back on an exception,
    then returned to the pool. The same shape as `with connect() as c:` so call sites read identically."""
    with pool().connection() as conn:
        yield conn


def close_pool() -> None:
    global _POOL
    if _POOL is not None:
        _POOL.close()
        _POOL = None


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
