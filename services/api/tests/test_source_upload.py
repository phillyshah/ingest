"""Uploading a PDF runs it through the same pipeline as anything fetched from the web (spec §5)."""

from __future__ import annotations

import hashlib

import pytest
from moveai_ingestion.config import FIXTURES
from moveai_ingestion.pipeline import run_all

FIXTURE = FIXTURES / "permitted-sources" / "owned_demo_protocol_with_photo.pdf"
SIDECAR = FIXTURES / "permitted-sources" / "owned_demo_protocol_with_photo.pdf.extraction.json"


@pytest.fixture(autouse=True)
def upload_root(tmp_path, monkeypatch):
    """Uploads must never land inside the checked-out fixtures tree during a test run. Local dev defaults
    ALLOWED_FILE_ROOTS to FIXTURES on purpose (every dev fixture already lives there); every deployment that
    actually accepts uploads sets it explicitly (infra/.env.example: /srv/moveai/uploads). Tests get their own
    throwaway root either way. The env var, not a module attribute: `allowed_file_roots()` re-reads it on every
    call, and both the upload endpoint and the fetch stage's path-containment check need to agree on the same
    root, which two separately-patched function references could not guarantee."""
    monkeypatch.setenv("ALLOWED_FILE_ROOTS", str(tmp_path))
    return tmp_path


def _upload(admin, filename: str = "protocol.pdf", content: bytes | None = None, title: str = "Uploaded protocol"):
    return admin.c.post(
        "/v1/sources/upload",
        headers=admin.h,
        files={"file": (filename, content or FIXTURE.read_bytes(), "application/pdf")},
        data={"title": title},
    )


def _place_sidecar(upload_root) -> bytes:
    # The mock extraction model is deterministic from a sidecar next to the file it reads (ADR-0006). Real content
    # addressing means the uploaded copy does not live at the fixture's own path, so the sidecar is placed at the
    # exact destination this upload will compute — the same sha256, the same naming — rather than relaxing the
    # storage layout to suit the mock.
    content = FIXTURE.read_bytes()
    sha = hashlib.sha256(content).hexdigest()
    (upload_root / "clinician-uploads").mkdir(parents=True, exist_ok=True)
    (upload_root / "clinician-uploads" / f"{sha}.pdf.extraction.json").write_text(SIDECAR.read_text())
    return content


def test_uploading_a_pdf_enqueues_it_and_it_runs_the_full_pipeline(admin, conn, upload_root):
    content = _place_sidecar(upload_root)
    r = _upload(admin, content=content)
    assert r.status_code == 202, r.text
    body = r.json()
    assert body["allowlist_state"] == "approved"  # the operator's own upload; nothing to allowlist-approve

    done = run_all(conn)
    assert all(st == "succeeded" for _, st in done), done
    variant = conn.execute(
        "select v.* from exercise_variant_version v join dependency_edge d on d.downstream_id=v.id where d.upstream_id=%s",
        (body["source_version_id"],),
    ).fetchone()
    assert variant["name"] == "Heel slide (active)"

    media = conn.execute("select * from media_asset_version where variant_version_id=%s", (variant["id"],)).fetchall()
    assert len(media) == 1 and media[0]["media_state"] == "graphic_available"

    # the Files tab can answer "what did my upload produce?" from the list alone
    row = next(s for s in admin.get("/sources?limit=200").json()["items"] if s["id"] == body["id"])
    assert row["source_type"] == "clinician_upload" and row["uploaded_by"]
    assert row["variants"] == 1 and row["awaiting_review"] == 1 and row["approved"] == 0 and row["photos"] == 1
    assert row["jobs_active"] == 0 and row["last_problem"] is None


def test_upload_grants_full_ownership_rights_except_training(admin, conn):
    r = _upload(admin)
    grant = conn.execute("select * from rights_grant where source_version_id=%s", (r.json()["source_version_id"],)).fetchone()
    assert grant["can_fetch"] == "allowed" and grant["can_display_to_patient"] == "allowed"
    assert grant["can_train_model"] == "denied"


def test_a_non_pdf_is_refused(admin):
    r = _upload(admin, filename="not-a-pdf.txt", content=b"just some text, not a pdf")
    assert r.status_code == 422
    assert r.json()["code"] == "not_a_pdf"


def test_an_oversized_upload_is_refused(admin, monkeypatch):
    import moveai_api.routers.sources as sources_module

    monkeypatch.setattr(sources_module, "MAX_DOCUMENT_BYTES", 10)
    r = _upload(admin)
    assert r.status_code == 413


def test_a_pt_cannot_upload_a_source(pt):
    r = pt.c.post(
        "/v1/sources/upload",
        headers=pt.h,
        files={"file": ("protocol.pdf", FIXTURE.read_bytes(), "application/pdf")},
        data={"title": "x"},
    )
    assert r.status_code == 403


def test_uploading_the_same_bytes_twice_reuses_the_same_stored_file(admin, upload_root):
    r1 = _upload(admin, title="First upload")
    r2 = _upload(admin, title="Second upload")
    assert r1.status_code == 202 and r2.status_code == 202
    assert r1.json()["id"] == r2.json()["id"], "the same bytes must resolve to the same source, not a duplicate"
    stored = list((upload_root / "clinician-uploads").glob("*.pdf"))
    assert len(stored) == 1


def test_the_stored_photo_is_downloadable_through_the_review_api(admin, conn, upload_root):
    content = _place_sidecar(upload_root)
    r = _upload(admin, content=content)
    assert r.status_code == 202, r.text
    run_all(conn)
    # Scoped to this upload's own source version — the seeded content packs installed for every test in this
    # suite may themselves carry media rows, and a bare `limit 1` would happily grab one of those instead.
    m = conn.execute(
        "select m.id from media_asset_version m join exercise_variant_version v on v.id=m.variant_version_id "
        "join dependency_edge d on d.downstream_id=v.id where d.upstream_id=%s",
        (r.json()["source_version_id"],),
    ).fetchone()
    assert m, "the pipeline should have linked the embedded photo to its one exercise"
    out = admin.c.get(f"/v1/media/{m['id']}/file", headers=admin.h)
    assert out.status_code == 200
    assert out.headers["content-type"] == "image/png"
    assert out.content.startswith(b"\x89PNG")


def test_a_media_id_with_nothing_stored_is_a_clean_404(admin):
    import uuid

    out = admin.c.get(f"/v1/media/{uuid.uuid4()}/file", headers=admin.h)
    assert out.status_code == 404
