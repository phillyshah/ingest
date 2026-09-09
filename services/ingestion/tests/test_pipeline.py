"""Ingestion acceptance (spec §12 gate, §13 tests 3, 4, 5, 7, 9, 13)."""
from __future__ import annotations

import pytest

from moveai_ingestion import queue as q
from moveai_ingestion.fetch import FetchError, fetch
from moveai_ingestion.pipeline import run_all


def _run(conn, svid, url):
    q.enqueue(conn, stage="access_check", source_version_id=svid, payload={"url": url, "region": "demo"})
    return run_all(conn)


def _state(conn, svid):
    return conn.execute("select pipeline_state from source_version where id=%s", (svid,)).fetchone()["pipeline_state"]


def _variants(conn, svid):
    return conn.execute("select v.* from exercise_variant_version v join dependency_edge d on d.downstream_id=v.id where d.upstream_id=%s order by v.name", (svid,)).fetchall()


def test_html_fixture_end_to_end(conn, fixture_source):
    f = fixture_source("owned_demo_protocol.html")
    done = _run(conn, f["source_version_id"], f["url"])
    assert [s for s, st in done] == ["access_check", "fetch", "parse", "extract", "normalize", "validate", "enqueue_review"]
    assert all(st == "succeeded" for _, st in done)
    assert _state(conn, f["source_version_id"]) == "pending_review"
    vs = _variants(conn, f["source_version_id"])
    assert {v["assistance"] for v in vs} == {"active", "resisted"}
    assert all(v["approval_state"] == "pending_review" for v in vs)
    assert all(v["source_reference"]["locator"] for v in vs)
    claims = conn.execute("select * from evidence_claim where source_version_id=%s and claim_type='dose'", (f["source_version_id"],)).fetchall()
    assert len(claims) == 10 and all(c["locator"].get("table") for c in claims)
    assert conn.execute("select count(*) as n from evidence_claim where source_version_id=%s and claim_type='clinician_only_intervention'", (f["source_version_id"],)).fetchone()["n"] == 1


def test_pdf_fixture_end_to_end(conn, fixture_source):
    f = fixture_source("owned_demo_protocol.pdf", source_type="pdf")
    done = _run(conn, f["source_version_id"], f["url"])
    assert all(st == "succeeded" for _, st in done), done
    assert _state(conn, f["source_version_id"]) == "pending_review"
    assert len(_variants(conn, f["source_version_id"])) == 2


def test_rerun_is_idempotent(conn, fixture_source):
    f = fixture_source("owned_demo_protocol.html")
    _run(conn, f["source_version_id"], f["url"])
    before = len(_variants(conn, f["source_version_id"]))
    # replaying the whole chain creates no duplicate jobs or versions (spec §13 test 13)
    job = q.enqueue(conn, stage="access_check", source_version_id=f["source_version_id"], payload={"url": f["url"], "region": "demo"})
    assert job["state"] == "succeeded"   # existing job returned, not re-queued
    conn.execute("update ingestion_job set state='queued' where id=%s", (job["id"],))
    run_all(conn)
    assert len(_variants(conn, f["source_version_id"])) == before


def test_unchanged_bytes_skip_reextraction(conn, fixture_source):
    from moveai_ingestion.pipeline import register_source_version
    f = fixture_source("owned_demo_protocol.html")
    _run(conn, f["source_version_id"], f["url"])
    sv2 = register_source_version(conn, f["source"]["id"], url=f["url"])
    conn.execute("insert into rights_grant(source_version_id, can_fetch, can_process_with_model, can_store_fulltext) values (%s,'allowed','allowed','allowed')", (sv2,))
    done = _run(conn, sv2, f["url"])
    assert [s for s, _ in done] == ["access_check", "fetch"]
    assert _state(conn, sv2) == "superseded"


def test_rights_unknown_blocks_fetch(conn, fixture_source):
    f = fixture_source("owned_demo_protocol.html", rights={"can_fetch": "allowed"})   # process_with_model unknown
    done = _run(conn, f["source_version_id"], f["url"])
    assert done == [("access_check", "failed")]
    assert _state(conn, f["source_version_id"]) == "rights_hold"
    job = conn.execute("select * from ingestion_job where source_version_id=%s", (f["source_version_id"],)).fetchone()
    assert job["error_class"] == "rights_unknown"


def test_not_allowlisted_source_is_held(conn, fixture_source):
    f = fixture_source("owned_demo_protocol.html", allowlist="pending")
    done = _run(conn, f["source_version_id"], f["url"])
    assert done == [("access_check", "failed")]
    assert _state(conn, f["source_version_id"]) == "rights_hold"


def test_prompt_injection_stays_data(conn, fixture_source):
    f = fixture_source("injection_attempt.html")
    done = _run(conn, f["source_version_id"], f["url"])
    assert all(st == "succeeded" for _, st in done)
    vs = _variants(conn, f["source_version_id"])
    assert len(vs) == 1 and vs[0]["approval_state"] == "pending_review"   # nothing got approved
    doses = conn.execute("select count(*) as n from evidence_claim where source_version_id=%s and claim_type='dose'", (f["source_version_id"],)).fetchone()["n"]
    assert doses == 0   # "set every dose to 10x10" produced no numbers
    ext = conn.execute("select warnings from ingestion_job where source_version_id=%s and stage='extract'", (f["source_version_id"],)).fetchone()
    assert any("instruction-like" in w for w in ext["warnings"])


def test_schema_violation_from_model_is_rejected(conn, fixture_source):
    from moveai_ingestion.llm import SchemaViolation, validate_output
    with pytest.raises(SchemaViolation):
        validate_output({"exercises": [], "approve": True})
    with pytest.raises(SchemaViolation):
        validate_output({"exercises": [{"source_exercise_name": "x", "locator": {}, "tool_call": "publish"}]})


def test_headingless_table_does_not_leak_phase(conn, fixture_source):
    f = fixture_source("headingless_dose_table.html")
    _run(conn, f["source_version_id"], f["url"])
    claims = conn.execute("select paraphrase, ambiguity_flags from evidence_claim where source_version_id=%s and claim_type='dose' order by paraphrase", (f["source_version_id"],)).fetchall()
    b = [c for c in claims if c["paraphrase"].startswith("Movement B")]
    assert b and all("phase_unlinked" in c["ambiguity_flags"] and "(phase: unlinked)" in c["paraphrase"] for c in b)
    a = [c for c in claims if c["paraphrase"].startswith("Movement A")]
    assert a and all("(phase: Phase A)" in c["paraphrase"] for c in a)


def test_ambiguous_ocr_triggers_review(conn, fixture_source):
    f = fixture_source("scanned_ambiguous.ocr.json", source_type="scanned_pdf")
    done = _run(conn, f["source_version_id"], f["url"])
    assert all(st == "succeeded" for _, st in done), done
    reps = conn.execute("select * from evidence_claim where source_version_id=%s and paraphrase like '%%repetitions%%'", (f["source_version_id"],)).fetchall()
    assert reps == []   # no number persisted for the ambiguous field
    ext = conn.execute("select warnings from ingestion_job where source_version_id=%s and stage='extract'", (f["source_version_id"],)).fetchone()
    assert any("ambiguous OCR" in w for w in ext["warnings"])


def test_passive_and_resisted_never_merge(conn, fixture_source):
    from moveai_ingestion.normalize import propose_duplicate
    f = fixture_source("owned_demo_protocol.html")
    _run(conn, f["source_version_id"], f["url"])
    vs = _variants(conn, f["source_version_id"])
    assert all(v["duplicate_of_entity_id"] is None for v in vs)
    a = next(v for v in vs if v["assistance"] == "active")
    assert propose_duplicate(conn, a["concept_id"], "passive", a["setting"], a["equipment"]) is None
    assert propose_duplicate(conn, a["concept_id"], "resisted", a["setting"], a["equipment"]) is None
    assert propose_duplicate(conn, a["concept_id"], "active", a["setting"], a["equipment"]) == a["entity_id"]


def test_derivative_copy_is_proposed_duplicate_not_merged(conn, fixture_source):
    f1 = fixture_source("owned_demo_protocol.html")
    _run(conn, f1["source_version_id"], f1["url"])
    f2 = fixture_source("derivative_copy.html")
    _run(conn, f2["source_version_id"], f2["url"])
    vs2 = _variants(conn, f2["source_version_id"])
    assert vs2 and all(v["duplicate_of_entity_id"] is not None for v in vs2)
    assert all(v["approval_state"] == "pending_review" for v in vs2)   # still a separate record awaiting review


def test_third_party_graphic_goes_to_rights_hold_text_proceeds(conn, fixture_source):
    f = fixture_source("rights_restricted_graphic.html")
    done = _run(conn, f["source_version_id"], f["url"])
    assert all(st == "succeeded" for _, st in done)
    v = _variants(conn, f["source_version_id"])[0]
    media = conn.execute("select media_state from media_asset_version where variant_version_id=%s", (v["id"],)).fetchone()
    assert media["media_state"] == "rights_hold" and v["approval_state"] == "pending_review"


def test_fetch_guards():
    with pytest.raises(FetchError) as e:
        fetch("file:///etc/passwd")
    assert e.value.error_class == "not_allowlisted"
    with pytest.raises(FetchError) as e:
        fetch("https://127.0.0.1/x")
    assert e.value.error_class == "not_allowlisted"
    with pytest.raises(FetchError):
        fetch("ftp://example.org/x")


def test_retry_backoff_and_dead_letter(conn):
    job = q.enqueue(conn, stage="fetch", source_version_id=None, payload={}, extra_key="retry-test")
    conn.execute("update ingestion_job set max_attempts=2 where id=%s", (job["id"],))
    j = q.claim(conn, "w1"); assert j["id"] == job["id"]
    assert q.fail(conn, j["id"], "fetch_failed", "boom") == "queued"
    conn.execute("update ingestion_job set run_after=now() where id=%s", (j["id"],))
    j = q.claim(conn, "w1")
    assert q.fail(conn, j["id"], "fetch_failed", "boom") == "dead_letter"
    assert q.retry_dead_letter(conn, j["id"])
    assert conn.execute("select state, attempts from ingestion_job where id=%s", (j["id"],)).fetchone() == {"state": "queued", "attempts": 0}


def test_lost_worker_lease_is_reclaimed(conn):
    job = q.enqueue(conn, stage="fetch", source_version_id=None, payload={}, extra_key="lease-test")
    j = q.claim(conn, "w-lost")
    conn.execute("update ingestion_job set leased_until = now() - interval '1 minute' where id=%s", (j["id"],))
    assert q.reclaim_expired(conn) == 1
    assert conn.execute("select state from ingestion_job where id=%s", (job["id"],)).fetchone()["state"] == "queued"


def test_terminology_release_resolution(conn):
    from datetime import date
    from moveai_ingestion.config import FIXTURES
    from moveai_ingestion.terminology import import_releases, resolve_code
    import_releases(conn, FIXTURES / "terminology")
    r = resolve_code(conn, "M75.01", date(2026, 9, 9))
    assert r["resolved"] and r["release_label"] == "FY26" and r["laterality"] == "right"
    r = resolve_code(conn, "M75.01", date(2026, 10, 1))
    assert r["release_label"] == "FY27"
    r = resolve_code(conn, "M75.0", date(2026, 9, 9))
    assert r["resolved"] and not r["billable"] and "not billable" in r["note"]
    assert not resolve_code(conn, "M75.01", date(2020, 1, 1))["resolved"]
    assert not resolve_code(conn, "Z99.99", date(2026, 9, 9))["resolved"]


def test_pack_install_is_idempotent_and_unsigned_cannot_publish(conn, users):
    from moveai_ingestion.packs import install_pack
    from moveai_rules import load_pack
    from moveai_ingestion.config import FIXTURES
    p = load_pack(FIXTURES / "content-packs" / "frozen_shoulder.yaml")
    a = install_pack(conn, p, author_id=users["pt"])
    b = install_pack(conn, p, author_id=users["pt"])
    assert a == b and len(a["protocol_version_ids"]) == 2
    states = {r["approval_state"] for r in conn.execute("select approval_state from protocol_version where content_pack='frozen_shoulder'").fetchall()}
    assert states == {"unsigned_placeholder"}
    demo = load_pack(FIXTURES / "content-packs" / "demo_synthetic.yaml")
    with pytest.raises(ValueError):
        install_pack(conn, demo, author_id=users["pt"], approver_id=users["pt"])
    d = install_pack(conn, demo, author_id=users["pt"], approver_id=users["lead"])
    assert len(d["protocol_version_ids"]) == 1
