from __future__ import annotations

from collections.abc import Iterator

import psycopg
from fastapi import Request
from moveai_db import pool


def get_conn(request: Request) -> Iterator[psycopg.Connection]:
    """One pooled connection per request, shared by every dependency in it.

    Committed when the request completes normally, rolled back if the handler raises, and returned to the pool
    either way. Drawing from the pool rather than opening a connection per request is what keeps the API inside
    the deployed database's client ceiling (see moveai_db.pool).
    """
    conn = getattr(request.state, "conn", None)
    if conn is not None:
        yield conn
        return
    with pool().connection() as conn:
        request.state.conn = conn
        yield conn
