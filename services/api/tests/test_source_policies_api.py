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


# ---------------------------------------------------------------- reading the terms from the app
# Why this exists: the terms have to be on record before anyone can accept them, and the only way to get them
# there was a GitHub workflow. A publisher whose page was momentarily unreachable therefore stayed unacceptable,
# which is what "I can only accept five of them" actually was.
class _FakeResponse:
    def __init__(self, status_code: int, content: bytes = b"", url: str = "https://x/terms"):
        self.status_code, self.content, self.url = status_code, content, url


class _FakeClient:
    def __init__(self, response):
        self.response = response
        self.calls: list[str] = []

    def get(self, url):
        self.calls.append(url)
        return self.response

    def close(self):
        pass


def _patch_capture(monkeypatch, response):
    import httpx

    client = _FakeClient(response)
    monkeypatch.setattr(httpx, "Client", lambda **kw: client)
    return client


TERMS_HTML = b"<html><body><p>" + (b"You may reuse this material under the terms set out here. " * 12) + b"</p></body></html>"


def test_reading_the_terms_from_the_app_makes_a_publisher_acceptable(loaded, rights, monkeypatch):
    before = rights.get("/source-policies").json()
    stuck = next(i for i in before["items"] if i["domain"] == "medlineplus.gov")
    assert stuck["status"] == "terms_unread" and stuck["blocked_by"].startswith("Nobody has read")

    client = _patch_capture(monkeypatch, _FakeResponse(200, TERMS_HTML))
    r = rights.post("/source-policies/medlineplus.gov/capture", {})
    assert r.status_code == 200, r.text
    body = r.json()
    assert client.calls == ["https://medlineplus.gov/about/using/usingcontent/"]
    assert body["status"] == "awaiting_acceptance" and body["evidence_state"] == "captured"
    assert body["terms_excerpt"] and "reuse this material" in body["terms_excerpt"]

    # and now it can actually be accepted, which is the whole point
    signed = rights.post("/source-policies/medlineplus.gov/decision", {"decision": "sign", "note": "read it"}).json()
    assert signed["effective"] is True and signed["status"] == "readable"


def test_a_refusal_is_recorded_with_what_the_site_said(loaded, rights, monkeypatch):
    _patch_capture(monkeypatch, _FakeResponse(403))
    body = rights.post("/source-policies/www.physio-pedia.com/capture", {}).json()
    assert body["status"] == "terms_unread" and body["evidence_state"] == "unreachable"
    assert "403" in body["terms_error"] and "refuses automated clients" in body["terms_error"]
    assert "403" in body["blocked_by"], "the page must say what happened, not just that it failed"


def test_a_javascript_shell_is_not_mistaken_for_terms(loaded, rights, monkeypatch):
    _patch_capture(monkeypatch, _FakeResponse(200, b"<html><body><div id=root></div></body></html>"))
    body = rights.post("/source-policies/medlineplus.gov/capture", {}).json()
    assert body["evidence_state"] == "unreachable" and "rendered client-side" in body["terms_error"]
    assert rights.post("/source-policies/medlineplus.gov/decision", {"decision": "sign"}).status_code == 409


def test_a_pt_cannot_read_terms_on_demand(loaded, pt, monkeypatch):
    _patch_capture(monkeypatch, _FakeResponse(200, TERMS_HTML))
    assert pt.post("/source-policies/medlineplus.gov/capture", {}).status_code == 403


def test_an_unknown_domain_is_a_clean_404(loaded, rights):
    assert rights.post("/source-policies/nope.example/capture", {}).status_code == 404


def test_the_list_groups_publishers_by_what_they_are_waiting_for(loaded, rights, monkeypatch):
    """The counts the Sources page shows. Without these, "why only five?" has no answer on the page."""
    _patch_capture(monkeypatch, _FakeResponse(200, TERMS_HTML))
    rights.post("/source-policies/medlineplus.gov/capture", {})
    rights.post("/source-policies/medlineplus.gov/decision", {"decision": "sign", "note": "ok"})
    rights.post("/source-policies/www.jospt.org/capture", {})
    rights.post("/source-policies/www.jospt.org/decision", {"decision": "sign", "note": "read, but it forbids reuse"})
    rights.post("/source-policies/www.who.int/capture", {})
    rights.post("/source-policies/www.choosingwisely.org/decision", {"decision": "reject", "note": "needs permission"})

    body = rights.get("/source-policies").json()
    by = body["by_status"]
    assert by["readable"] == 1
    # signed and still unusable is its own bucket: nothing more to do, so it must not sit in the to-do pile
    assert by["accepted_but_unusable"] == 1
    assert by["awaiting_acceptance"] == 1 and by["rejected"] == 1
    assert by["terms_unread"] == body["total"] - 4
    assert sum(by.values()) == body["total"]
