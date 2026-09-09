from __future__ import annotations

import uuid
from typing import Any

import pytest

from moveai_contracts.api import PERMISSION_OPS
from moveai_db import J
from moveai_ingestion.config import FIXTURES
from moveai_ingestion.pipeline import register_source_version

ALL_ALLOWED = {op: "allowed" for op in PERMISSION_OPS} | {"can_train_model": "denied"}


def register_fixture_source(conn, name: str, *, rights: dict[str, str] | None = None, source_type: str = "html",
                            allowlist: str = "approved") -> dict[str, Any]:
    path = FIXTURES / "permitted-sources" / name
    url = f"file://{path}?fixture={uuid.uuid4().hex[:8]}"
    src = conn.execute(
        "insert into source(canonical_url, publisher, title, source_type, allowlist_state) values (%s,'MoveAI (owned)',%s,%s,%s) returning *",
        (url, name, source_type, allowlist)).fetchone()
    svid = register_source_version(conn, src["id"], url=url)
    r = rights if rights is not None else ALL_ALLOWED
    cols = {op: r.get(op, "unknown") for op in PERMISSION_OPS}
    conn.execute(f"insert into rights_grant(source_version_id, {','.join(cols)}, permission_evidence) values (%s,{','.join(['%s'] * len(cols))},%s)",
                 (svid, *cols.values(), J({"kind": "ownership"})))
    return {"source": src, "source_version_id": svid, "url": url}


@pytest.fixture()
def fixture_source(conn):
    return lambda name, **kw: register_fixture_source(conn, name, **kw)


@pytest.fixture()
def tenant(conn):
    return conn.execute("insert into tenant(name) values (%s) returning id", (f"t-{uuid.uuid4().hex[:6]}",)).fetchone()["id"]
