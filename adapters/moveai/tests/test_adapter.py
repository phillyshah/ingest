from __future__ import annotations

import json
import uuid

import httpx
from fastapi.testclient import TestClient

from moveai_adapter import MoveAIMockClient
from moveai_adapter.webhooks import dispatch_pending, sign
from moveai_api.db import get_conn
from moveai_api.main import app
from moveai_contracts.plan import DraftPatch
from moveai_planner.approval import approve, patch_draft
from moveai_planner.engine import plan_options
from moveai_planner.seed import seed


def _draft_and_approve(conn, s):
    from moveai_contracts.intake import CaseSubmission, Intake, IntakeField
    fields = {"affected_side": IntakeField.known("left"), "concerning_findings": IntakeField.known([]), "surgeon_restrictions": IntakeField.known("none"),
              "equipment": IntakeField.known(["elastic band"]), "prior_session_response": IntakeField(status="not_applicable")}
    r = plan_options(conn, tenant_id=s["tenant_id"], user_id=s["pt"], submission=CaseSubmission(case_ref="adapter", narrative="demo condition", intake=Intake(fields=fields)))
    patch_draft(conn, tenant_id=s["tenant_id"], user_id=s["pt"], plan_id=r.plan_id, patch=DraftPatch(expected_revision=1, selected_option_id=r.options[0].option_id))
    approve(conn, tenant_id=s["tenant_id"], user_id=s["pt"], roles=["pt"], plan_id=r.plan_id, expected_revision=2, attestation="ok")
    return r.plan_id


def test_adapter_reads_only_approved_and_dedups(conn):
    s = seed(conn)
    app.dependency_overrides[get_conn] = lambda: (yield conn)
    try:
        with TestClient(app, base_url="http://testserver/v1") as tc:
            client = MoveAIMockClient(base_url="http://testserver/v1", user_id=str(s["integration"]), tenant_id=str(s["tenant_id"]), http=tc)
            assert client.get_approved_plan(str(uuid.uuid4()))["available"] is False
            plan_id = _draft_and_approve(conn, s)
            got = client.get_approved_plan(plan_id)
            assert got["available"] and got["revision"] == 2 and got["approval_signature"]
            changes = client.poll_changes()
            assert changes and client.change_cursor > 0
            assert client.poll_changes() == []     # cursor advanced; nothing new
            assert not any(hasattr(client, m) for m in ("create_variant", "update_catalog", "publish"))
    finally:
        app.dependency_overrides.clear()
    body = json.dumps({"id": "evt-1", "type": "plan.approved"}).encode()
    hook = MoveAIMockClient(base_url="x", user_id="u", tenant_id="t", webhook_secret="s")
    assert hook.handle_webhook({"x-moveai-signature": sign("s", body)}, body)["id"] == "evt-1"
    assert hook.handle_webhook({"x-moveai-signature": sign("s", body)}, body) is None          # duplicate
    assert hook.handle_webhook({"x-moveai-signature": "bad"}, body) is None                    # bad signature


def test_outbox_dispatch_retries_and_dead_letters(conn):
    s = seed(conn)
    _draft_and_approve(conn, s)
    received = []

    def handler(req: httpx.Request) -> httpx.Response:
        received.append(req)
        return httpx.Response(500)

    st = dispatch_pending(conn, url="https://moveai.example/hook", secret="s", transport=httpx.MockTransport(handler))
    assert st["failed"] >= 1 and st["delivered"] == 0
    for _ in range(10):
        dispatch_pending(conn, url="https://moveai.example/hook", secret="s", transport=httpx.MockTransport(handler))
    assert conn.execute("select count(*) as n from outbox_event where dead_lettered").fetchone()["n"] >= 1
    ok = dispatch_pending(conn, url="https://moveai.example/hook", secret="s", transport=httpx.MockTransport(lambda r: httpx.Response(200)))
    assert ok["delivered"] == 0   # dead-lettered events are not retried automatically
    req = received[0]
    assert req.headers["x-moveai-signature"] == sign("s", req.content) and "narrative" not in req.content.decode()
