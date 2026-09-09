from __future__ import annotations

from collections.abc import Iterator

import psycopg
from fastapi import Request
from moveai_db import connect


def get_conn(request: Request) -> Iterator[psycopg.Connection]:
    conn = getattr(request.state, "conn", None)
    if conn is not None:
        yield conn
        return
    conn = connect()
    request.state.conn = conn
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
