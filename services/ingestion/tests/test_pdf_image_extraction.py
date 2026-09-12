"""Embedded PDF images, end to end: extracted, stored, and linked to the one exercise they belong to.

The model is never shown these images and cannot honestly say which exercise a photo belongs to (spec §5:
schema-only text output, no tools) — a PDF's embedded images have no URL to report the way an HTML `<img src>`
does. Association is entirely deterministic, done in code from the real page number the parser found the
exercise's own name on, and it must refuse to guess the moment there is more than one candidate on a page.
"""

from __future__ import annotations

import uuid

import pytest
from moveai_ingestion import queue as q
from moveai_ingestion.config import FIXTURES
from moveai_ingestion.parse import parse_pdf
from moveai_ingestion.pipeline import register_source_version, run_all
from moveai_ingestion.storage import get_storage

FIXTURE = FIXTURES / "permitted-sources" / "owned_demo_protocol_with_photo.pdf"


def test_parse_pdf_extracts_the_embedded_photo():
    doc = parse_pdf(FIXTURE.read_bytes())
    assert len(doc.images) == 1
    img = doc.images[0]
    assert img.page == 1
    assert img.content_type == "image/png"
    assert len(img.data) > 0


def test_an_unreadable_embedded_image_is_skipped_not_fatal(monkeypatch):
    """A malformed embedded image must not sink extraction of the rest of the document."""
    import pypdf

    real = pypdf._page.PageObject.images.fget  # noqa: SLF001

    def boom(self):
        raise ValueError("corrupt image stream")

    monkeypatch.setattr(pypdf._page.PageObject, "images", property(boom))  # noqa: SLF001
    doc = parse_pdf(FIXTURE.read_bytes())
    assert doc.images == []
    assert any("could not read embedded images" in w for w in doc.warnings)
    monkeypatch.setattr(pypdf._page.PageObject, "images", property(real))  # noqa: SLF001


@pytest.fixture()
def uploaded(conn):
    from moveai_contracts.api import PERMISSION_OPS
    from moveai_db import J

    all_allowed = {op: "allowed" for op in PERMISSION_OPS} | {"can_train_model": "denied"}
    url = f"file://{FIXTURE}?u={uuid.uuid4().hex[:8]}"
    src = conn.execute(
        "insert into source(canonical_url, publisher, title, source_type, allowlist_state) "
        "values (%s,'clinician upload','with-photo','clinician_upload','approved') returning *",
        (url,),
    ).fetchone()
    svid = register_source_version(conn, src["id"], url=url)
    cols = {op: all_allowed.get(op, "unknown") for op in PERMISSION_OPS}
    conn.execute(
        f"insert into rights_grant(source_version_id, {','.join(cols)}, permission_evidence) values (%s,{','.join(['%s'] * len(cols))},%s)",
        (svid, *cols.values(), J({"kind": "ownership"})),
    )
    return {"source_version_id": svid, "url": url}


def test_the_photo_reaches_the_catalog_linked_to_its_one_exercise(conn, uploaded):
    q.enqueue(
        conn, stage="access_check", source_version_id=uploaded["source_version_id"], payload={"url": uploaded["url"], "region": "demo"}
    )
    done = run_all(conn)
    assert all(st == "succeeded" for _, st in done), done

    variant = conn.execute(
        "select v.* from exercise_variant_version v join dependency_edge d on d.downstream_id=v.id where d.upstream_id=%s",
        (uploaded["source_version_id"],),
    ).fetchone()
    assert variant["name"] == "Heel slide (active)"

    media = conn.execute("select * from media_asset_version where variant_version_id=%s", (variant["id"],)).fetchall()
    assert len(media) == 1
    m = media[0]
    assert m["media_state"] == "graphic_available"
    assert m["media_type"] == "still_graphic"
    assert m["content_sha256"]
    assert m["storage_ref"]
    # the actual bytes are retrievable, not merely referenced
    assert get_storage().get(m["storage_ref"])


def test_two_candidate_exercises_on_one_page_get_flagged_not_guessed(conn):
    """The core safety property: ambiguity is surfaced, never silently resolved by picking one."""
    from moveai_contracts.api import PERMISSION_OPS
    from moveai_db import J
    from moveai_ingestion.llm import ExtractedExercise, ExtractionResult
    from moveai_ingestion.normalize import persist

    all_allowed = {op: "allowed" for op in PERMISSION_OPS} | {"can_train_model": "denied"}
    url = f"file://{FIXTURE}?u={uuid.uuid4().hex[:8]}"
    src = conn.execute(
        "insert into source(canonical_url, source_type, allowlist_state) values (%s,'clinician_upload','approved') returning *",
        (url,),
    ).fetchone()
    svid = register_source_version(conn, src["id"], url=url)
    cols = {op: all_allowed.get(op, "unknown") for op in PERMISSION_OPS}
    conn.execute(
        f"insert into rights_grant(source_version_id, {','.join(cols)}, permission_evidence) values (%s,{','.join(['%s'] * len(cols))},%s)",
        (svid, *cols.values(), J({"kind": "ownership"})),
    )

    result = ExtractionResult(
        exercises=[
            ExtractedExercise(source_exercise_name="Heel slide (active)", assistance="active", locator={"page": 1}, steps=["a"]),
            ExtractedExercise(source_exercise_name="Ankle pump", assistance="active", locator={"page": 1}, steps=["b"]),
        ]
    )
    page_text = [{"page": 1, "text": "Heel slide (active)"}, {"page": 1, "text": "Ankle pump"}]
    extracted_images = [
        {"page": 1, "index": 0, "storage_ref": "local://x", "sha256": "a" * 64, "content_type": "image/png", "byte_size": 10}
    ]

    out = persist(
        conn,
        source_version_id=svid,
        source_row=src,
        result=result,
        review_flags=[],
        excerpt_allowed=True,
        extracted_images=extracted_images,
        page_text=page_text,
        rights_grant_id=None,
    )
    assert out["media"]["linked"] == 0
    variants = conn.execute(
        "select name, extraction_warnings from exercise_variant_version where id = any(%s)", (out["variants"],)
    ).fetchall()
    for v in variants:
        assert any("could not be uniquely matched" in w for w in v["extraction_warnings"])
    assert conn.execute("select count(*) as n from media_asset_version").fetchone()["n"] == 0


def test_an_image_on_a_page_with_no_recognisable_exercise_is_reported_not_dropped(conn):
    from moveai_ingestion.llm import ExtractionResult
    from moveai_ingestion.normalize import persist

    src = conn.execute(
        "insert into source(canonical_url, source_type, allowlist_state) values (%s,'clinician_upload','approved') returning *",
        (f"file:///tmp/x-{uuid.uuid4().hex[:6]}",),
    ).fetchone()
    svid = register_source_version(conn, src["id"], url=src["canonical_url"])
    extracted_images = [
        {"page": 3, "index": 0, "storage_ref": "local://y", "sha256": "b" * 64, "content_type": "image/png", "byte_size": 10}
    ]
    out = persist(
        conn,
        source_version_id=svid,
        source_row=src,
        result=ExtractionResult(),
        review_flags=[],
        extracted_images=extracted_images,
        page_text=[],
    )
    assert out["media"]["linked"] == 0
    assert out["media"]["unmatched_images"] == [
        {"page": 3, "count": 1, "note": "1 image(s) on page 3 could not be uniquely matched to one exercise; attach manually"}
    ]
