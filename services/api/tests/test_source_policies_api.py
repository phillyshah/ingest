"""The allowlist over HTTP: who may read it, who may decide, and what the page is told."""

from __future__ import annotations

import hashlib

import pytest
from moveai_db import J
from moveai_ingestion.source_policies import sync


@pytest.fixture()
def loaded(conn):
    sync(conn)
    return conn


def capture(conn, domain: str, text: str = "The terms of use for this site." * 20) -> str:
    sha = hashlib.sha256(text.encode()).hexdigest()
    conn.execute(
        "update source_policy set evidence=%s, evidence_state='captured' where domain=%s",
        (J({"sha256": sha, "fetched_at": "2026-09-12T00:00:00Z", "quoted_span": text[:300]}), domain),
    )
    return sha


def test_anyone_signed_in_can_see_which_publishers_are_readable(loaded, pt):
    """Not privileged information, and a campaign returning nothing is unreadable without it."""
    r = pt.get("/source-policies")
    assert r.status_code == 200
    body = r.json()
    assert body["total"] > 0 and body["readable"] == 0
    one = next(i for i in body["items"] if i["domain"] == "medlineplus.gov")
    assert one["blocked_by"] == "Nobody has read this publisher's licence terms yet."
    assert one["permissions"]["can_fetch"] == "allowed", "the licence permits it; the policy is what does not"
    assert one["effective"] is False


def test_a_pt_cannot_accept_licence_terms(loaded, pt):
    capture(loaded, "medlineplus.gov")
    assert pt.post("/source-policies/medlineplus.gov/decision", {"decision": "sign"}).status_code == 403


def test_a_rights_reviewer_can_accept_terms_that_have_been_read(loaded, rights):
    capture(loaded, "medlineplus.gov")
    r = rights.post("/source-policies/medlineplus.gov/decision", {"decision": "sign", "note": "public domain, read 2026-09-12"})
    assert r.status_code == 200
    assert r.json()["effective"] is True
    assert r.json()["blocked_by"] is None


def test_terms_nobody_has_read_cannot_be_accepted(loaded, rights):
    r = rights.post("/source-policies/www.cdc.gov/decision", {"decision": "sign"})
    assert r.status_code == 409
    assert "not been fetched" in r.json()["message"]


def test_rejecting_a_publisher_requires_a_reason(loaded, rights):
    assert rights.post("/source-policies/www.jospt.org/decision", {"decision": "reject"}).status_code == 422
    r = rights.post("/source-policies/www.jospt.org/decision", {"decision": "reject", "note": "subscription only"})
    assert r.status_code == 200
    assert r.json()["blocked_by"] == "Rejected: subscription only"
