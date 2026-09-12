"""Sign-in by username, and removing a campaign from the board.

The dev-login route is unauthenticated by necessity — it is what produces an identity — so the tests that matter
most here are the ones proving it is unreachable outside the development shim.
"""

from __future__ import annotations

import uuid

import pytest


def login(client, username: str):
    return client.post("/v1/dev-login", json={"username": username})


# ---------------------------------------------------------------- sign-in
def test_a_seeded_user_signs_in_by_name(client, seeded):
    r = login(client, "admin")
    assert r.status_code == 200
    body = r.json()
    assert body["user_id"] == str(seeded["admin"])
    assert body["tenant_id"] == str(seeded["tenant_id"])
    assert "source_admin" in body["roles"]


def test_the_name_is_case_insensitive(client, seeded):
    assert login(client, "ADMIN").json()["user_id"] == str(seeded["admin"])


def test_an_unknown_name_is_created_with_every_role(client, conn, seeded):
    name = f"moveai{uuid.uuid4().hex[:6]}"
    body = login(client, name).json()
    assert body["display_name"] == name
    # Every role, so the header switcher can reach the review queues without a second account.
    enum_roles = {r["v"] for r in conn.execute("select unnest(enum_range(null::user_role))::text as v").fetchall()}
    assert set(body["roles"]) == enum_roles
    assert body["role"] == "source_admin"


def test_signing_in_twice_reuses_the_same_user(client, seeded):
    name = f"moveai{uuid.uuid4().hex[:6]}"
    assert login(client, name).json()["user_id"] == login(client, name).json()["user_id"]


def test_an_empty_name_is_refused(client, seeded):
    assert login(client, "   ").status_code == 400


def test_it_is_unreachable_in_production(client, seeded, monkeypatch):
    """The guard that matters: an unauthenticated identity factory must not exist in production."""
    monkeypatch.setenv("MOVEAI_ENV", "production")
    assert login(client, "admin").status_code == 404


def test_it_is_unreachable_when_supabase_auth_is_on(client, seeded, monkeypatch):
    monkeypatch.setenv("AUTH_MODE", "supabase")
    assert login(client, "admin").status_code == 404


# ---------------------------------------------------------------- delete
def draft(admin, title="throwaway"):
    r = admin.post("/ingestion-campaigns", {"title": title, "scope": {"ailment_text": "frozen shoulder", "codes": []}})
    assert r.status_code == 201, r.text
    return r.json()["id"]


def test_a_draft_can_be_deleted_and_leaves_the_board(admin):
    cid = draft(admin)
    assert any(c["id"] == cid for c in admin.get("/ingestion-campaigns").json()["items"])

    assert admin.delete(f"/ingestion-campaigns/{cid}").status_code == 200
    assert not any(c["id"] == cid for c in admin.get("/ingestion-campaigns").json()["items"])


def test_the_row_is_retained_rather_than_erased(admin, conn, seeded):
    """Soft on purpose: audit_event references the campaign and is append-only."""
    cid = draft(admin)
    admin.delete(f"/ingestion-campaigns/{cid}")
    row = conn.execute("select deleted_at, deleted_by from ingestion_campaign where id=%s", (cid,)).fetchone()
    assert row is not None and row["deleted_at"] is not None
    assert str(row["deleted_by"]) == str(seeded["admin"])


def test_deleting_twice_is_not_an_error(admin):
    cid = draft(admin)
    admin.delete(f"/ingestion-campaigns/{cid}")
    r = admin.delete(f"/ingestion-campaigns/{cid}")
    assert r.status_code == 200 and r.json()["already"] is True


def test_a_campaign_that_has_run_is_refused(admin, conn):
    """Its jobs and any review decisions against them are real work; hiding it would hide those too."""
    cid = draft(admin)
    sv = conn.execute("select current_scope_version_id as v from ingestion_campaign where id=%s", (cid,)).fetchone()["v"]
    conn.execute("insert into campaign_run(campaign_id, scope_version_id, run_number) values (%s,%s,1)", (cid, sv))
    r = admin.delete(f"/ingestion-campaigns/{cid}")
    assert r.status_code == 409 and r.json()["code"] == "campaign_has_run"
    assert any(c["id"] == cid for c in admin.get("/ingestion-campaigns").json()["items"])


def test_only_a_source_admin_may_delete(admin, pt):
    cid = draft(admin)
    assert pt.delete(f"/ingestion-campaigns/{cid}").status_code == 403


def test_an_unknown_campaign_is_a_404(admin):
    assert admin.delete(f"/ingestion-campaigns/{uuid.uuid4()}").status_code == 404


# ---------------------------------------------------------------- version
def test_version_reports_what_is_running(client):
    body = client.get("/v1/version").json()
    assert set(body) == {"version", "commit", "built_at", "environment"}


@pytest.mark.parametrize("path", ["/v1/version", "/v1/healthz"])
def test_ops_endpoints_need_no_identity(client, path):
    assert client.get(path).status_code == 200
