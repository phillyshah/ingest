"""Seed the database: users, terminology, content packs, catalog release, and the permitted HTML/PDF fixtures ingested."""

from __future__ import annotations

import os
import pathlib
import uuid

os.environ.setdefault("PLAN_SIGNING_SECRET", "dev-only-secret-change-me")
from moveai_contracts.api import PERMISSION_OPS  # noqa: E402
from moveai_db import J, connect  # noqa: E402
from moveai_ingestion import queue as q  # noqa: E402
from moveai_ingestion.config import FIXTURES  # noqa: E402
from moveai_ingestion.pipeline import register_source_version, run_all  # noqa: E402
from moveai_planner.seed import seed  # noqa: E402


def ingest_fixture(conn, name: str, rights: dict[str, str], source_type: str = "html", tenant_id=None):
    url = f"file://{FIXTURES / 'permitted-sources' / name}"
    src = conn.execute("select * from source where canonical_url=%s", (url,)).fetchone()
    if src:
        return None
    src = conn.execute(
        "insert into source(canonical_url, publisher, title, source_type, allowlist_state, intended_uses) values (%s,'MoveAI (owned synthetic fixture)',%s,%s,'approved','{reference,clinician}') returning *",
        (url, name, source_type),
    ).fetchone()
    svid = register_source_version(conn, src["id"], url=url)
    cols = {op: rights.get(op, "unknown") for op in PERMISSION_OPS}
    conn.execute(
        f"insert into rights_grant(source_version_id, {','.join(cols)}, permission_evidence) values (%s,{','.join(['%s'] * len(cols))},%s)",
        (svid, *cols.values(), J({"kind": "ownership", "text": "owned synthetic fixture"})),
    )
    q.enqueue(
        conn,
        stage="access_check",
        source_version_id=svid,
        payload={"url": url, "region": "demo"},
        tenant_id=tenant_id,
    )
    return svid


ALL = {op: "allowed" for op in PERMISSION_OPS} | {"can_train_model": "denied"}
TEXT_ONLY = {**ALL, "can_download_media": "unknown", "can_display_to_patient": "unknown"}

if __name__ == "__main__":
    with connect() as conn:
        out = seed(conn)
        print("tenant", out["tenant_id"], "release", out["release"]["label"] if out["release"] else None)
        for name, rights, st in (
            ("owned_demo_protocol.html", ALL, "html"),
            ("owned_demo_protocol.pdf", ALL, "pdf"),
            ("headingless_dose_table.html", ALL, "html"),
            ("injection_attempt.html", ALL, "html"),
            ("rights_restricted_graphic.html", TEXT_ONLY, "html"),
            ("scanned_ambiguous.ocr.json", ALL, "scanned_pdf"),
        ):
            ingest_fixture(conn, name, rights, st, out["tenant_id"])
        done = run_all(conn)
        conn.commit()
        print(
            f"ingestion: {sum(1 for _, s in done if s == 'succeeded')} stages succeeded, {sum(1 for _, s in done if s != 'succeeded')} held/failed"
        )
        env = [f"E2E_TENANT_ID={out['tenant_id']}"]
        for k in ("admin", "rights", "pt", "pt2", "lead", "auditor", "integration"):
            print(f"  {k:12} X-User-Id={out[k]}")
            env.append(f"E2E_{k.upper()}_ID={out[k]}")
        outdir = pathlib.Path(".demo-out")
        outdir.mkdir(exist_ok=True)
        (outdir / "e2e.env").write_text("\n".join(env) + "\n")
        print("  ids written to .demo-out/e2e.env (for the UI sign-in and Playwright smoke)")
        print("seed complete;", uuid.uuid4().hex[:0])
