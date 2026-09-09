"""API contract and role enforcement (spec §10, §11, §13, §21G)."""

from __future__ import annotations

import uuid

from moveai_ingestion.config import FIXTURES
from moveai_ingestion.pipeline import run_all

FIX = FIXTURES / "permitted-sources"
ALL_ALLOWED = {
    k: "allowed"
    for k in (
        "can_fetch",
        "can_store_fulltext",
        "can_store_excerpt",
        "can_embed_for_search",
        "can_process_with_model",
        "can_display_to_clinician",
        "can_display_to_patient",
        "can_redistribute",
        "can_transform",
    )
}


def _register_and_ingest(admin, conn, name="owned_demo_protocol.html", rights=ALL_ALLOWED, approve=True):
    r = admin.post(
        "/sources",
        json={
            "canonical_url": "https://example.invalid/x",
            "source_type": "html",
            "publisher": "MoveAI",
            "title": name,
            "local_path": f"{FIX / name}?t={uuid.uuid4().hex[:6]}",
            "rights": {"permissions": rights, "permission_evidence": {"kind": "ownership"}},
        },
    )
    assert r.status_code == 201, r.text
    sid = r.json()["id"]
    if approve:
        assert admin.patch(f"/sources/{sid}", json={"allowlist_state": "approved"}).status_code == 200
    j = admin.post("/ingestion-jobs", json={"source_id": sid})
    assert j.status_code == 202, j.text
    run_all(conn)
    return sid, j.json()["job_id"], j.json()["source_version_id"]


def test_health_and_me(client, pt, admin):
    assert client.get("/v1/healthz").json() == {"status": "ok"}
    assert client.get("/v1/me").status_code == 401
    assert pt.get("/me").json()["roles"] == ["pt"]
    assert admin.get("/me", headers={"X-Role": "pt"}).status_code == 403


def test_source_to_review_to_publish(admin, pt, pt2, lead, conn):
    sid, job_id, svid = _register_and_ingest(admin, conn)
    job = admin.get(f"/ingestion-jobs/{job_id}").json()
    assert job["state"] == "succeeded" and job["pipeline_state"] == "pending_review"
    q = pt.get("/reviews/queue?entity_table=exercise_variant_version").json()
    mine = [i for i in q["items"] if i["name"].startswith("Demo movement")]
    assert len(mine) == 2
    vid = mine[0]["version_id"]
    d = pt.get(f"/reviews/exercise_variant_version/{vid}").json()
    assert d["source"]["excerpt_allowed"] and d["source"]["url"] and d["record"]["source_reference"]["locator"]
    assert d["claims"] and d["missing_dose_fields"] == []
    # published-only default hides it from plan users
    assert all(x["id"] != vid for x in pt.get("/exercises?q=Demo").json()["items"])
    assert any(x["id"] == vid for x in pt.get("/exercises?q=Demo&include_unpublished=true").json()["items"])
    assert d["duplicate_proposal"] is not None
    # the ingested variants duplicate the seeded demo-pack variants: approval is blocked until the proposal is resolved
    assert d["duplicate_proposal"] is not None
    r = pt.post(
        "/reviews",
        json={"entity_table": "exercise_variant_version", "version_id": vid, "decision": "approve"},
    )
    assert r.status_code == 409 and r.json()["code"] == "duplicate_unresolved"
    r = pt.post(
        "/reviews",
        json={
            "entity_table": "exercise_variant_version",
            "version_id": vid,
            "decision": "reject",
            "reason": "duplicate of catalog variant",
        },
    )
    assert r.status_code == 201 and r.json()["approval_state"] == "rejected"
    # a distinct variant: approve, then edit -> invalidates approval and creates a pending version
    _register_and_ingest(admin, conn, name="headingless_dose_table.html")
    vid = next(
        i["version_id"]
        for i in pt.get("/reviews/queue?entity_table=exercise_variant_version").json()["items"]
        if i["name"].startswith("Movement A")
    )
    r = pt.post(
        "/reviews",
        json={
            "entity_table": "exercise_variant_version",
            "version_id": vid,
            "decision": "approve",
            "reason": "ok",
            "time_spent_seconds": 40,
        },
    )
    assert r.status_code == 201 and r.json()["approval_state"] == "approved"
    r = pt2.post(
        "/reviews",
        json={
            "entity_table": "exercise_variant_version",
            "version_id": vid,
            "decision": "edit",
            "changes": {"cues": ["keep it slow"]},
            "reason": "clarity",
        },
    )
    assert r.status_code == 201 and r.json()["invalidated_prior_approval"] and r.json()["approval_state"] == "pending_review"
    assert (
        conn.execute("select approval_state from exercise_variant_version where id=%s", (vid,)).fetchone()["approval_state"]
        == "invalidated"
    )
    new_vid = r.json()["new_version_id"]
    # author of the new version cannot approve it
    assert (
        pt2.post(
            "/reviews",
            json={"entity_table": "exercise_variant_version", "version_id": new_vid, "decision": "approve"},
        ).status_code
        == 403
    )
    assert (
        pt.post(
            "/reviews",
            json={"entity_table": "exercise_variant_version", "version_id": new_vid, "decision": "approve"},
        ).status_code
        == 201
    )
    # only the clinical lead publishes
    assert pt.post("/catalog-releases", json={"label": "r-pt"}).status_code == 403
    rel = lead.post("/catalog-releases", json={"label": f"r-{uuid.uuid4().hex[:6]}"})
    assert rel.status_code == 201 and rel.json()["item_count"] > 0
    assert any(x["id"] == new_vid for x in pt.get("/exercises?q=Movement").json()["items"])
    ch = pt.get("/changes").json()
    assert any(c["version_id"] == new_vid and c["change_type"] == "published" for c in ch["items"])


def test_rights_unknown_source_is_held_and_visible(admin, conn):
    sid, job_id, svid = _register_and_ingest(admin, conn, rights={"can_fetch": "allowed"})
    job = admin.get(f"/ingestion-jobs/{job_id}").json()
    assert (
        job["state"] == "failed"
        and job["error_class"] == "rights_unknown"
        and job["pipeline_state"] == "rights_hold"
        and not job["retry_eligible"]
    )


def test_merge_refuses_distinct_assistance(admin, pt, conn):
    _register_and_ingest(admin, conn)
    rows = conn.execute(
        "select id, entity_id, assistance from exercise_variant_version where name like 'Demo movement%%' and approval_state='pending_review' order by name"
    ).fetchall()
    a = next(r for r in rows if r["assistance"] == "active")
    b = next(r for r in rows if r["assistance"] == "resisted")
    r = pt.post(
        "/reviews",
        json={
            "entity_table": "exercise_variant_version",
            "version_id": str(b["id"]),
            "decision": "merge",
            "merge_into_entity_id": str(a["entity_id"]),
        },
    )
    assert r.status_code == 409 and r.json()["code"] == "distinct_variant"


def test_rights_denial_holds_media_and_withdrawal_propagates(admin, pt, rights, lead, conn):
    _, _, svid = _register_and_ingest(admin, conn, name="rights_restricted_graphic.html")
    media = conn.execute(
        "select m.id, m.variant_version_id from media_asset_version m join exercise_variant_version v on v.id=m.variant_version_id "
        "join dependency_edge d on d.downstream_id=v.id where d.upstream_id=%s",
        (svid,),
    ).fetchone()
    assert (
        conn.execute("select media_state from media_asset_version where id=%s", (media["id"],)).fetchone()["media_state"] == "rights_hold"
    )
    # text approval proceeds regardless of the graphic
    assert (
        pt.post(
            "/reviews",
            json={
                "entity_table": "exercise_variant_version",
                "version_id": str(media["variant_version_id"]),
                "decision": "approve",
            },
        ).status_code
        == 201
    )
    # withdrawal of the source propagates
    assert (
        pt.post(
            "/reviews",
            json={
                "entity_table": "source_version",
                "version_id": svid,
                "decision": "withdraw",
                "reason": "publisher retraction",
            },
        ).status_code
        == 403
    )
    r = lead.post(
        "/reviews",
        json={
            "entity_table": "source_version",
            "version_id": svid,
            "decision": "withdraw",
            "reason": "publisher retraction",
        },
    )
    assert r.status_code == 201 and any(v.startswith("exercise_variant_version") for v in r.json()["propagation"]["affected_versions"])
    assert (
        conn.execute("select approval_state from exercise_variant_version where id=%s", (media["variant_version_id"],)).fetchone()[
            "approval_state"
        ]
        == "withdrawn"
    )


def test_plan_options_roles_and_three_families(pt, admin, integration):
    for text in (
        "55-year-old slightly obese man with frozen shoulder",
        "Patient recovering after surgical MCL repair",
        "Patient with a hamstring pull, no surgery",
    ):
        r = pt.post("/plan-options", json={"case_ref": f"c-{uuid.uuid4().hex[:6]}", "narrative": text})
        assert r.status_code == 200, r.text
        b = r.json()
        assert (
            b["status"] == "needs_assessment"
            and b["prescription"] is None
            and b["assignable"] is False
            and b["pathway_previews"]
            and b["missing_fields"]
        )
    assert admin.post("/plan-options", json={"case_ref": "x", "narrative": "frozen shoulder"}).status_code == 403
    assert integration.get(f"/plans/{uuid.uuid4()}/approved").json()["available"] is False


def test_plan_draft_approve_retrieve_via_api(pt, admin, integration, seeded):
    fields = {
        "affected_side": {"status": "known", "value": "left"},
        "concerning_findings": {"status": "known", "value": []},
        "surgeon_restrictions": {"status": "known", "value": "none"},
        "equipment": {"status": "known", "value": ["elastic band"]},
        "prior_session_response": {"status": "not_applicable"},
        "age": {"status": "known", "value": 55, "unit": "years"},
    }
    r = pt.post(
        "/plan-options",
        json={"case_ref": "api-demo", "narrative": "demo condition", "intake": {"fields": fields}},
    )
    assert r.status_code == 200 and r.json()["status"] == "draft_ready", r.text
    plan_id = r.json()["plan_id"]
    opt = r.json()["options"][0]
    assert integration.get(f"/plans/{plan_id}/approved").json()["available"] is False
    p = pt.patch(f"/plans/{plan_id}/draft", json={"expected_revision": 1, "selected_option_id": opt["option_id"]})
    assert p.status_code == 200 and p.json()["revision"] == 2
    assert pt.post(f"/plans/{plan_id}/approve", json={"expected_revision": 1, "attestation": "x"}).status_code == 409
    assert admin.post(f"/plans/{plan_id}/approve", json={"expected_revision": 2, "attestation": "x"}).status_code == 403
    a = pt.post(
        f"/plans/{plan_id}/approve",
        json={"expected_revision": 2, "attestation": "reviewed"},
        headers={"Idempotency-Key": "k1"},
    )
    assert a.status_code == 200 and a.json()["status"] == "approved"
    replay = pt.post(
        f"/plans/{plan_id}/approve",
        json={"expected_revision": 2, "attestation": "reviewed"},
        headers={"Idempotency-Key": "k1"},
    )
    assert replay.status_code == 200 and replay.headers.get("idempotent-replay") == "true"
    assert (
        pt.post(
            f"/plans/{plan_id}/approve",
            json={"expected_revision": 2, "attestation": "other"},
            headers={"Idempotency-Key": "k1"},
        ).status_code
        == 409
    )
    got = integration.get(f"/plans/{plan_id}/approved").json()
    assert got["available"] and got["revision"] == 2 and got["content_hash"] == a.json()["content_hash"]
    # cross-tenant retrieval is refused
    other = As_other(integration, seeded)
    assert (
        other.get(f"/plans/{plan_id}/approved").status_code in (403, 404)
        or other.get(f"/plans/{plan_id}/approved").json()["available"] is False
    )
    # reassessment creates a new draft; the approved revision stays
    ra = pt.post(f"/plans/{plan_id}/reassessments", json={"narrative": "demo condition", "intake": {"fields": fields}})
    assert ra.status_code == 200 and ra.json()["status"] == "draft_ready"
    assert integration.get(f"/plans/{plan_id}/approved").json()["revision"] == 2


def As_other(base, seeded):
    class OtherTenant:
        def get(self, url):
            return base.c.get(
                "/v1" + url,
                headers={"X-User-Id": str(seeded["integration"]), "X-Tenant-Id": str(uuid.uuid4())},
            )

    return OtherTenant()


def test_protocol_draft_rejects_unsupported_rules(pt, seeded):
    body = {
        "condition_code": "adhesive_capsulitis",
        "name": "Tenant pathway",
        "rules": [
            {
                "key": "r1",
                "name": "bad",
                "expression": {"op": "regex", "field": "age", "value": ".*"},
                "action": {"type": "inform"},
            }
        ],
    }
    assert pt.post("/protocols", json=body).status_code == 422
    body["rules"] = [
        {
            "key": "r1",
            "name": "ok",
            "expression": {"op": "known", "field": "affected_side"},
            "action": {"type": "require_field", "field": "affected_side", "reason": "side"},
        }
    ]
    r = pt.post("/protocols", json=body)
    assert r.status_code == 201 and r.json()["rule_previews"][0].startswith("IF affected_side is known THEN require_field")


def test_campaign_lifecycle(admin, pt, lead, conn):
    scope = {
        "ailment_text": "surgically repaired MCL tear",
        "codes": ["S83.411A"],
        "limits": {"max_sources": 2, "max_usd": 1.0, "max_search_requests": 10},
        "supplied_source_urls": [
            f"file://{FIX / 'owned_demo_protocol.html'}?c={uuid.uuid4().hex[:6]}",
            "https://unknown.example/protocol.pdf",
        ],
        "source_policy": "allowlist_and_supplied",
        "service_date": "2026-09-09",
    }
    # pt cannot create campaigns
    assert pt.post("/ingestion-campaigns", json={"title": "x", "scope": scope}).status_code == 403
    r = admin.post("/ingestion-campaigns", json={"title": "MCL repair rehab", "scope": scope})
    assert r.status_code == 201, r.text
    c = r.json()
    cid = c["id"]
    assert c["lifecycle"] == "draft" and "start" in c["allowed_actions"]
    pv = admin.post(f"/ingestion-campaigns/{cid}/scope-preview").json()
    assert (
        pv["resolved_codes"][0]["resolved"]
        and pv["resolved_codes"][0]["release_label"] == "FY26"
        and "right" == pv["resolved_codes"][0]["laterality"]
    )
    assert pv["interpreted_conditions"][0]["code"] == "mcl_tear_surgical_repair"
    assert not pv["can_start"] and "scope not confirmed" in pv["blocking_reasons"]
    # draft dispatches nothing
    assert conn.execute("select count(*) as n from ingestion_job where campaign_id=%s", (cid,)).fetchone()["n"] == 0
    assert admin.post(f"/ingestion-campaigns/{cid}/runs").status_code == 422
    # confirm scope, then start; the file source is pending allowlist, unknown domain is pending
    admin.patch(f"/ingestion-campaigns/{cid}", json={"scope": {**scope, "scope_confirmed": True}})
    r = admin.post(f"/ingestion-campaigns/{cid}/runs")
    assert r.status_code == 202, r.text
    c = r.json()
    # two supplied sources are pending allowlist (never fetched); the pack's approved reference source is the one dispatched
    assert c["counts"].get("sources_pending", 0) == 2 and c["counts"].get("sources_new", 0) == 1 and c["lifecycle"] == "running"
    dispatched = conn.execute(
        "select s.allowlist_state from ingestion_job j join source_version sv on sv.id=j.source_version_id join source s on s.id=sv.source_id where j.campaign_id=%s",
        (cid,),
    ).fetchall()
    assert [d["allowlist_state"] for d in dispatched] == ["approved"]
    rev = c["run_revision"]
    # drag to complete is refused
    assert admin.post(f"/ingestion-campaigns/{cid}/transition", json={"to": "complete"}).status_code == 409
    # pause/resume with revision checks
    assert admin.post(f"/ingestion-campaigns/{cid}/pause", json={"expected_revision": rev + 5}).status_code == 409
    c = admin.post(f"/ingestion-campaigns/{cid}/pause", json={"expected_revision": rev}).json()
    assert c["control"] == "paused" and "resume" in c["allowed_actions"]
    c = admin.post(f"/ingestion-campaigns/{cid}/resume", json={"expected_revision": c["run_revision"]}).json()
    assert c["control"] == "active"
    # the reference source has unknown rights: the job fails permanently, the run finishes, the card needs attention with visible reasons
    run_all(conn)
    c = admin.get(f"/ingestion-campaigns/{cid}").json()
    assert (
        c["lifecycle"] == "needs_attention"
        and any("pending allowlist" in b for b in c["blockers"])
        and c["runs"][-1]["state"] == "finished"
    )
    jobs = admin.get(f"/ingestion-jobs?campaign_id={cid}").json()["items"]
    assert jobs and jobs[0]["state"] == "failed" and jobs[0]["error_class"] == "rights_unknown"
    c = admin.post(f"/ingestion-campaigns/{cid}/cancel", json={"expected_revision": c["run_revision"], "reason": "test"})
    assert c.status_code == 409  # no active run to cancel once finished
    # a fresh run can be started and then cancelled; results and audit history are retained
    c = admin.post(f"/ingestion-campaigns/{cid}/runs").json()
    c = admin.post(f"/ingestion-campaigns/{cid}/cancel", json={"expected_revision": c["run_revision"], "reason": "test"}).json()
    assert c["control"] == "cancelled" and c["lifecycle"] == "closed_incomplete"
    assert admin.get("/ingestion-campaigns").json()["total"] == 0  # archived by default
    assert admin.get("/ingestion-campaigns?include_archived=true").json()["total"] == 1
    ev = admin.get(f"/ingestion-campaigns/{cid}/events").json()["items"]
    assert [e["event"] for e in ev if e["event"] != "job"] == [
        "created",
        "scope_revised",
        "started",
        "paused",
        "resumed",
        "started",
        "cancelled",
    ]
    dash = admin.get("/ingestion-campaigns/dashboard").json()
    assert "pt_backlog" in dash and "spend_today_usd" in dash


def test_campaign_with_approved_source_runs_and_limits_hold(admin, pt, conn):
    # register + approve an owned source with rights, linked to the demo condition through its claims
    url = f"file://{FIX / 'owned_demo_protocol.html'}?c={uuid.uuid4().hex[:6]}"
    r = admin.post(
        "/sources",
        json={
            "canonical_url": url,
            "source_type": "html",
            "publisher": "MoveAI",
            "title": "owned",
            "rights": {"permissions": ALL_ALLOWED},
        },
    )
    sid = r.json()["id"]
    admin.patch(f"/sources/{sid}", json={"allowlist_state": "approved"})
    scope = {
        "ailment_text": "demo condition",
        "limits": {"max_sources": 1, "max_usd": 0.04, "max_search_requests": 10},
        "supplied_source_urls": [url, url + "&dup=1"],
        "source_policy": "supplied_only",
        "scope_confirmed": True,
    }
    c = admin.post("/ingestion-campaigns", json={"title": "Demo campaign", "scope": scope}).json()
    # budget cap below the per-source reservation: nothing dispatches, reason visible
    c = admin.post(f"/ingestion-campaigns/{c['id']}/runs").json()
    assert c["counts"].get("sources_new", 0) == 0 and c["lifecycle"] == "needs_attention"
    ev = admin.get(f"/ingestion-campaigns/{c['id']}/events").json()["items"][-1]
    assert any("budget" in s["reason"] for s in ev["detail"]["skipped"])
    # raise the cap via an audited scope revision and start a new run
    c = admin.patch(
        f"/ingestion-campaigns/{c['id']}",
        json={"scope": {**scope, "limits": {"max_sources": 1, "max_usd": 1.0, "max_search_requests": 10}}},
    ).json()
    c = admin.post(f"/ingestion-campaigns/{c['id']}/runs").json()
    assert c["counts"]["sources_new"] == 1 and c["lifecycle"] == "running"
    run_all(conn)
    c = admin.get(f"/ingestion-campaigns/{c['id']}").json()
    assert c["lifecycle"] == "pt_review" and c["counts"]["awaiting_review"] == 2 and c["counts"]["sources_processed"] == 1
    assert c["runs"][-1]["state"] == "finished"
    ex = admin.get(f"/ingestion-campaigns/{c['id']}/exercises").json()
    assert len(ex["variants"]) == 2 and ex["claims"]
    q = pt.get(f"/reviews/queue?campaign_id={c['id']}").json()
    assert q["total"] == 2
    costs = admin.get(f"/ingestion-campaigns/{c['id']}/costs").json()
    assert costs["ledger"] and costs["by_stage"]
