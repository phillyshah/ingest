"""Ingestion-side fixes from the September 2026 backend review.

Each test names the defect it pins down; the bug ledger in the review report is the index.
"""

from __future__ import annotations

import httpx
import pytest
from moveai_ingestion import queue as q
from moveai_ingestion.campaigns import scope_preview
from moveai_ingestion.config import FIXTURES
from moveai_ingestion.fetch import HTTP_HEADERS, FetchError, fetch_http
from moveai_ingestion.packs import install_pack
from moveai_ingestion.pipeline import run_all
from moveai_rules import load_pack

LIMITS = {
    "max_sources": 5,
    "max_candidates": 5,
    "max_search_requests": 5,
    "max_runtime_seconds": 600,
    "max_usd": 1,
    "max_document_bytes": 1_000_000,
}


# ---------------------------------------------------------------- chain was silently dropped from packs
def test_open_closed_chain_survives_pack_install(conn, users):
    """`chain: closed` in mcl_repair.yaml used to vanish: PackVariant had no such field and pydantic ignored it."""
    install_pack(conn, load_pack(FIXTURES / "content-packs" / "mcl_repair.yaml"), author_id=users["pt"])
    chains = {r["chain"] for r in conn.execute("select chain from exercise_variant_version where chain is not null").fetchall()}
    assert "closed" in chains


def test_pack_rejects_a_chain_value_the_column_would_refuse():
    from moveai_rules.pack import PackVariant

    with pytest.raises(ValueError):
        PackVariant(key="x", name="x", assistance="active", region="knee", chain="sideways")


# ---------------------------------------------------------------- fetcher headers and redirect parking
class _Transport(httpx.BaseTransport):
    def __init__(self, handler):
        self.handler = handler
        self.seen: list[httpx.Request] = []

    def handle_request(self, request):
        self.seen.append(request)
        return self.handler(request)


def _patch_client(monkeypatch, transport):
    real = httpx.Client

    def factory(**kw):
        kw["transport"] = transport
        return real(**kw)

    monkeypatch.setattr(httpx, "Client", factory)


def test_fetcher_sends_the_headers_publishers_expect(monkeypatch):
    """The capture script learned publishers 403 a bare User-Agent; the campaign fetcher had never caught up."""
    monkeypatch.setenv("ALLOWED_FETCH_DOMAINS", "pub.example")
    import moveai_ingestion.fetch as fetch_module

    monkeypatch.setattr(fetch_module, "_is_private", lambda host: False)
    t = _Transport(lambda r: httpx.Response(200, headers={"content-type": "text/html"}, content=b"<html><body>ok</body></html>"))
    _patch_client(monkeypatch, t)
    out = fetch_http("https://pub.example/page")
    assert out.content_type == "text/html"
    sent = t.seen[0].headers
    assert sent["accept"] == HTTP_HEADERS["Accept"] and sent["accept-language"] == HTTP_HEADERS["Accept-Language"]
    assert "MoveAI-Ingest" in sent["user-agent"]  # still honest about what it is


def test_redirect_off_the_allowlist_names_the_host_instead_of_failing_opaquely(monkeypatch):
    monkeypatch.setenv("ALLOWED_FETCH_DOMAINS", "old.example")
    import moveai_ingestion.fetch as fetch_module

    monkeypatch.setattr(fetch_module, "_is_private", lambda host: False)
    t = _Transport(lambda r: httpx.Response(301, headers={"location": "https://www.new.example/guide"}))
    _patch_client(monkeypatch, t)
    with pytest.raises(FetchError) as e:
        fetch_http("https://old.example/guide")
    assert e.value.error_class == "redirected_off_allowlist"
    assert e.value.redirect_target == "https://www.new.example/guide"
    assert "www.new.example" in str(e.value)


def test_a_redirect_target_is_parked_as_a_pending_campaign_source(conn, users, monkeypatch):
    """The fetch stage rolls back on failure; the parked item must be written outside that savepoint."""
    monkeypatch.setenv("ALLOWED_FETCH_DOMAINS", "old.example")
    import moveai_ingestion.fetch as fetch_module

    monkeypatch.setattr(fetch_module, "_is_private", lambda host: False)
    t = _Transport(lambda r: httpx.Response(302, headers={"location": "https://www.new.example/guide.pdf"}))
    _patch_client(monkeypatch, t)

    from moveai_ingestion import campaigns

    tenant = conn.execute("insert into tenant(name) values ('t-redirect') returning id").fetchone()["id"]
    created = campaigns.create(
        conn,
        tenant,
        users["admin"],
        {
            "title": "r",
            "scope": {
                "ailment_text": None,
                "codes": [],
                "limits": LIMITS,
                "supplied_source_urls": [],
                "source_policy": "allowlist_only",
                "exclusion": {},
                "refinements": {},
            },
        },
    )
    camp = created["id"]
    scope_version = conn.execute("select current_scope_version_id as id from ingestion_campaign where id=%s", (camp,)).fetchone()["id"]
    run = conn.execute(
        "insert into campaign_run(campaign_id, scope_version_id, run_number, state) values (%s,%s,1,'running') returning id",
        (camp, scope_version),
    ).fetchone()["id"]
    src = conn.execute(
        "insert into source(tenant_id, canonical_url, source_type, allowlist_state) values (%s,'https://old.example/guide','html','approved') returning id",
        (tenant,),
    ).fetchone()["id"]
    svid = conn.execute(
        "insert into source_version(source_id, final_url, pipeline_state) values (%s,'https://old.example/guide','discovered') returning id",
        (src,),
    ).fetchone()["id"]
    from moveai_contracts.api import PERMISSION_OPS

    cols = ", ".join(PERMISSION_OPS)
    conn.execute(
        f"insert into rights_grant(source_version_id, {cols}) values (%s,{','.join(['%s'] * len(PERMISSION_OPS))})",
        (svid, *["allowed"] * len(PERMISSION_OPS)),
    )
    q.enqueue(
        conn,
        stage="fetch",
        source_version_id=svid,
        payload={"url": "https://old.example/guide"},
        tenant_id=tenant,
        campaign_id=camp,
        campaign_run_id=run,
    )
    done = run_all(conn)
    assert done and done[0][1] in ("failed", "dead_letter")
    parked = conn.execute(
        "select ci.detail, s.canonical_url from campaign_item ci join source s on s.id=ci.item_id where ci.campaign_id=%s and ci.item_table='source' and ci.disposition='pending'",
        (camp,),
    ).fetchone()
    assert parked and parked["canonical_url"] == "https://www.new.example/guide.pdf"
    assert parked["detail"]["domain"] == "www.new.example" and "no publisher policy" in parked["detail"]["reason"]


# ---------------------------------------------------------------- strategy text tells the truth
def test_strategy_text_no_longer_implies_the_campaign_searches_a_publisher(conn):
    out = scope_preview(
        conn,
        None,
        {
            "ailment_text": None,
            "codes": [],
            "limits": LIMITS,
            "supplied_source_urls": [],
            "scope_confirmed": False,
            "exclusion": {},
            "refinements": {},
        },
    )
    assert not any(s.startswith("allowlisted publisher:") for s in out["proposed_strategy"])
    assert any("no automatic discovery" in s for s in out["proposed_strategy"])


# ---------------------------------------------------------------- web graphics carry the rights grant
def test_a_referenced_web_graphic_is_linked_to_the_sources_rights_grant(conn, fixture_source):
    """Without the link, a later rights denial could never find the media row to put it on hold."""
    f = fixture_source("rights_restricted_graphic.html")
    q.enqueue(conn, stage="access_check", source_version_id=f["source_version_id"], payload={"url": f["url"], "region": "demo"})
    run_all(conn)
    media = conn.execute(
        "select m.rights_grant_id from media_asset_version m join exercise_variant_version v on v.id=m.variant_version_id "
        "join dependency_edge d on d.downstream_id=v.id where d.upstream_id=%s",
        (f["source_version_id"],),
    ).fetchall()
    assert media and all(m["rights_grant_id"] is not None for m in media)
    grant = conn.execute("select id from rights_grant where source_version_id=%s", (f["source_version_id"],)).fetchone()
    assert {m["rights_grant_id"] for m in media} == {grant["id"]}


# ---------------------------------------------------------------- the terminology fixture now covers replacement and sports injuries
def test_joint_replacement_and_sports_injury_codes_resolve_with_laterality(conn):
    from datetime import date

    from moveai_ingestion.terminology import import_releases, resolve_code

    import_releases(conn, FIXTURES / "terminology")
    day = date(2026, 6, 1)
    knee = resolve_code(conn, "Z96.651", day)
    assert knee["resolved"] and knee["laterality"] == "right" and "knee" in knee["descriptor"].lower()
    hip = resolve_code(conn, "Z96.643", day)
    assert hip["resolved"] and hip["laterality"] == "bilateral"
    shoulder = resolve_code(conn, "M75.121", day)
    assert shoulder["resolved"] and shoulder["laterality"] == "right"
    acl = resolve_code(conn, "S83.512D", day)
    assert acl["resolved"] and acl["laterality"] == "left" and "subsequent" in acl["descriptor"]
    category = resolve_code(conn, "M17", day)
    assert category["resolved"] and category["billable"] is False and "search scope" in category["note"]
    assert resolve_code(conn, "S93.401A", day)["resolved"]  # ankle sprain
    assert resolve_code(conn, "Z47.1", day)["resolved"]  # aftercare following joint replacement
