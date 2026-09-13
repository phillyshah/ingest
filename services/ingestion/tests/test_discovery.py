"""Automatic source discovery (review milestone M7).

What these pin: a campaign with no URLs now has somewhere to look; what it finds is ranked by the condition's own
names; a page is still only fetched from a publisher whose terms are signed; everything else found is parked with
a reason and picked up by a later run once the terms are accepted; and the ceilings hold.
"""

from __future__ import annotations

import gzip
import hashlib
import uuid

import pytest
from moveai_db import J
from moveai_ingestion import campaigns, discovery
from moveai_ingestion import queue as q
from moveai_ingestion.discovery import (
    MockSearch,
    Terms,
    condition_terms,
    is_disallowed,
    load_vocabulary,
    parse_robots,
    parse_sitemap,
    refresh_index,
    score_url,
    search_queries,
)
from moveai_ingestion.pipeline import run_job
from moveai_ingestion.source_policies import sign, sync

LIMITS = {
    "max_sources": 5,
    "max_candidates": 20,
    "max_search_requests": 3,
    "max_runtime_seconds": 600,
    "max_usd": 5,
    "max_document_bytes": 5_000_000,
}

TKA = {
    "internal_code": "test_tka",
    "preferred_name": "Total knee arthroplasty, primary",
    "synonyms": ["TKA", "TKR", "total knee replacement", "knee replacement"],
    "body_region": "knee",
}


# ---------------------------------------------------------------- pure parsing and ranking
def test_condition_terms_come_only_from_the_condition_row():
    t = condition_terms([TKA])
    assert "knee replacement" in t.phrases and "total knee arthroplasty" in t.phrases
    assert t.acronyms == ("tka", "tkr")


def test_scoring_prefers_rehab_pages_and_needs_the_condition_named():
    t, v = condition_terms([TKA]), load_vocabulary()
    rehab, _ = score_url("https://www.nhs.uk/conditions/knee-replacement/recovery/", t, v)
    news, _ = score_url("https://www.nhs.uk/news/knee-replacement-waiting-times/", t, v)
    unrelated, _ = score_url("https://www.nhs.uk/conditions/asthma/", t, v)
    exercises_only, _ = score_url("https://www.nhs.uk/live-well/exercise/", t, v)
    assert rehab > news > 0
    assert unrelated == 0 and exercises_only == 0  # "exercise" alone never makes a page a candidate


def test_acronyms_match_whole_tokens_only():
    t, v = Terms(phrases=(), acronyms=("tka",)), load_vocabulary()
    assert score_url("https://x.org/tka-rehab-protocol", t, v)[0] > 0
    assert score_url("https://x.org/heart-attack-recovery", t, v)[0] == 0
    assert score_url("https://x.org/page", t, v, title="TKA exercises after surgery")[0] > 0


def test_robots_parsing_is_conservative():
    sitemaps, disallow = parse_robots(
        "User-agent: *\nDisallow: /search\nDisallow: /account/\n\nUser-agent: moveai-ingest\nDisallow: /private\n\nUser-agent: other\nDisallow: /\nSitemap: https://x.org/sitemap.xml\n"
    )
    assert sitemaps == ["https://x.org/sitemap.xml"]
    assert disallow == ["/account/", "/private", "/search"]
    assert is_disallowed("https://x.org/search?q=1", disallow) and not is_disallowed("https://x.org/conditions/", disallow)
    assert is_disallowed("https://x.org/anything", ["/"])


def test_sitemap_index_urlset_and_gzip_parse_without_an_xml_parser():
    kind, entries = parse_sitemap(b'<?xml version="1.0"?><sitemapindex><sitemap><loc>https://x.org/a.xml</loc></sitemap></sitemapindex>')
    assert kind == "index" and entries == [{"loc": "https://x.org/a.xml", "lastmod": None}]
    raw = b"<urlset><url><loc>https://x.org/p?a=1&amp;b=2</loc><lastmod>2026-01-01</lastmod></url></urlset>"
    kind, entries = parse_sitemap(gzip.compress(raw))
    assert kind == "urlset" and entries == [{"loc": "https://x.org/p?a=1&b=2", "lastmod": "2026-01-01"}]
    # an entity bomb is inert text to a regex
    kind, entries = parse_sitemap(
        b'<!DOCTYPE x [<!ENTITY a "aaaa"><!ENTITY b "&a;&a;">]><urlset><url><loc>https://x.org/&b;</loc></url></urlset>'
    )
    assert entries[0]["loc"] == "https://x.org/&b;"


def test_search_queries_use_plain_names_and_site_restrictions():
    qs = search_queries([TKA], ["medlineplus.gov"])
    assert ("Total knee arthroplasty exercises", "search") in qs
    assert ("knee replacement exercises", "search") in qs or ("total knee replacement exercises", "search") in qs
    assert any(v == "search_site" and "site:medlineplus.gov" in query for query, v in qs)


# ---------------------------------------------------------------- the cached site index
SITES = {
    "https://medlineplus.gov/robots.txt": b"User-agent: *\nDisallow: /search/\nSitemap: https://medlineplus.gov/sitemap_index.xml\n",
    "https://medlineplus.gov/sitemap_index.xml": b"<sitemapindex><sitemap><loc>https://medlineplus.gov/s1.xml</loc></sitemap><sitemap><loc>https://elsewhere.example/s.xml</loc></sitemap></sitemapindex>",
    "https://medlineplus.gov/s1.xml": b"""<urlset>
        <url><loc>https://medlineplus.gov/kneereplacement.html</loc></url>
        <url><loc>https://medlineplus.gov/ency/patientinstructions/knee-replacement-exercises.html</loc></url>
        <url><loc>https://medlineplus.gov/search/knee-replacement</loc></url>
        <url><loc>https://medlineplus.gov/asthma.html</loc></url>
        <url><loc>https://other.example/knee-replacement</loc></url>
    </urlset>""",
    "https://www.nhs.uk/robots.txt": b"User-agent: *\nDisallow:\n",
    "https://www.nhs.uk/sitemap.xml": b"<urlset><url><loc>https://www.nhs.uk/conditions/knee-replacement/recovery/</loc></url><url><loc>https://www.nhs.uk/conditions/knee-replacement/</loc></url><url><loc>https://www.nhs.uk/news/knee-replacement-funding/</loc></url></urlset>",
}


@pytest.fixture()
def offline_sites(monkeypatch):
    calls: list[str] = []

    def fake_get(url, domain):
        calls.append(url)
        return SITES.get(url)

    monkeypatch.setattr(discovery, "_get", fake_get)
    return calls


@pytest.fixture()
def policies(conn, users):
    sync(conn)
    return conn


def _sign(conn, domain):
    text = "These terms permit reuse." * 20
    sha = hashlib.sha256(text.encode()).hexdigest()
    conn.execute(
        "update source_policy set evidence=%s, evidence_state='captured' where domain=%s",
        (J({"sha256": sha, "fetched_at": "2026-09-12T00:00:00Z", "quoted_span": text[:200]}), domain),
    )
    rid = conn.execute("select id from app_user where 'rights_reviewer' = any(roles) limit 1").fetchone()["id"]
    return sign(conn, domain, rid)


def test_refresh_index_reads_robots_and_sitemaps_once_and_respects_disallow(policies, offline_sites):
    idx = refresh_index(policies, "medlineplus.gov")
    assert idx["state"] == "fetched" and idx["sitemap_urls"] == [
        "https://medlineplus.gov/sitemap_index.xml",
        "https://medlineplus.gov/s1.xml",
    ]
    urls = {r["url"] for r in policies.execute("select url from publisher_page where domain='medlineplus.gov'").fetchall()}
    assert "https://medlineplus.gov/kneereplacement.html" in urls
    assert "https://medlineplus.gov/search/knee-replacement" not in urls  # robots Disallow honoured
    assert not any("other.example" in u or "elsewhere.example" in u for u in urls)  # a sitemap cannot point us off the domain
    n = len(offline_sites)
    refresh_index(policies, "medlineplus.gov")
    assert len(offline_sites) == n  # cached: nothing re-fetched within the TTL


def test_refresh_index_records_a_publisher_with_no_sitemap(policies, offline_sites):
    idx = refresh_index(policies, "www.cdc.gov")
    assert idx["state"] == "unreachable" and idx["url_count"] == 0


# ---------------------------------------------------------------- the discover stage, end to end (offline)
def _condition(conn):
    return conn.execute(
        "insert into condition(internal_code, preferred_name, synonyms, body_region) values (%s,%s,%s,%s) returning *",
        (f"{TKA['internal_code']}_{uuid.uuid4().hex[:6]}", TKA["preferred_name"], TKA["synonyms"], TKA["body_region"]),
    ).fetchone()


def _campaign(conn, tenants, users, cond, **scope):
    body = {
        "title": "TKA discovery",
        "scope": {
            "ailment_text": None,
            "codes": [],
            "limits": LIMITS,
            "supplied_source_urls": [],
            "source_policy": "allowlist_only",
            "scope_confirmed": True,
            "exclusion": {},
            "refinements": {},
            "additional_condition_ids": [str(cond["id"])],
            **scope,
        },
    }
    return campaigns.create(conn, tenants["tenant_a"], users["admin"], body)


def _run_discover_job(conn):
    job = q.claim(conn, "test-worker", stages=("discover",))
    assert job is not None, "start() should have enqueued a discover job"
    assert run_job(conn, job) == "succeeded", conn.execute("select last_error from ingestion_job where id=%s", (job["id"],)).fetchone()
    return job


def test_a_campaign_with_no_urls_finds_pages_reads_signed_ones_and_parks_the_rest(policies, offline_sites, tenants, users, monkeypatch):
    conn = policies
    monkeypatch.setenv("DISCOVERY_PROVIDERS", "sitemap,search")
    monkeypatch.setenv("SEARCH_PROVIDER", "mock")
    MockSearch.results = {
        "knee": [
            {"url": "https://www.nhs.uk/conditions/knee-replacement/recovery/", "title": "Recovery - Knee replacement - NHS"},
            {"url": "https://www.someclinic.example/knee-replacement-exercises", "title": "Knee replacement exercises"},
            {"url": "https://www.someclinic.example/about-us", "title": "About us"},
        ]
    }
    _sign(conn, "medlineplus.gov")  # readable; www.nhs.uk stays listed-but-unsigned
    cond = _condition(conn)
    c = _campaign(conn, tenants, users, cond)
    c = campaigns.start(conn, tenants["tenant_a"], users["admin"], c["id"])
    assert c["lifecycle"] == "running" and c["counts"].get("sources_new", 0) == 0  # nothing to read yet: discovery is queued
    run = conn.execute("select * from campaign_run where campaign_id=%s", (c["id"],)).fetchone()
    assert run["discovery_closed_at"] is None

    _run_discover_job(conn)
    c = campaigns.get(conn, tenants["tenant_a"], c["id"])

    # signed publisher: dispatched as jobs, best-scored pages first, none for the asthma page
    new = conn.execute(
        "select ci.detail from campaign_item ci where ci.campaign_id=%s and ci.item_table='source_version' order by ci.created_at",
        (c["id"],),
    ).fetchall()
    urls = [r["detail"]["url"] for r in new]
    assert urls and all("medlineplus.gov" in u for u in urls)
    assert "https://medlineplus.gov/ency/patientinstructions/knee-replacement-exercises.html" in urls
    assert not any("asthma" in u for u in urls)
    assert all(r["detail"]["via"] in ("sitemap", "search", "search_site") for r in new)
    jobs = conn.execute("select stage from ingestion_job where campaign_id=%s and stage='access_check'", (c["id"],)).fetchall()
    assert len(jobs) == len(urls)

    # unsigned listed publisher and an unlisted domain: parked, with the reason, grouped by publisher on the card
    pend = {p["domain"]: p for p in c["pending_publishers"]}
    assert "www.nhs.uk" in pend and pend["www.nhs.uk"]["publisher"] and "terms" in pend["www.nhs.uk"]["reason"]
    assert pend["www.nhs.uk"]["count"] >= 2 and any("recovery" in u for u in pend["www.nhs.uk"]["pages"])
    assert "www.someclinic.example" in pend and pend["www.someclinic.example"]["publisher"] is None
    assert "no publisher policy" in pend["www.someclinic.example"]["reason"]
    assert pend["www.someclinic.example"]["count"] == 1  # "about us" never named the condition
    assert not conn.execute(
        "select 1 from ingestion_job j join source_version sv on sv.id=j.source_version_id join source s on s.id=sv.source_id where j.campaign_id=%s and s.canonical_url like '%%nhs.uk%%'",
        (c["id"],),
    ).fetchone()

    # the search ceiling held: three queries reserved, the rest skipped
    ev = conn.execute("select detail from campaign_event where campaign_id=%s and event='discovered'", (c["id"],)).fetchone()["detail"]
    ran = [s for s in ev["search"] if "hits" in s]
    assert len(ran) == LIMITS["max_search_requests"] and any("skipped" in s for s in ev["search"])
    assert conn.execute("select budget from campaign_run where id=%s", (run["id"],)).fetchone()["budget"]["search_requests"] == 3
    assert ev["publishers"] and any(p["domain"] == "www.nhs.uk" and not p["readable"] and p["matches"] >= 2 for p in ev["publishers"])

    # discovery is closed once the job is done; the run keeps going on the dispatched pages
    run = conn.execute("select * from campaign_run where id=%s", (run["id"],)).fetchone()
    assert run["state"] == "running" and run["discovery_closed_at"] is None  # access_check jobs are still queued
    assert "start" not in c["allowed_actions"]


def test_accepting_terms_then_starting_again_picks_up_the_parked_pages(policies, offline_sites, tenants, users, monkeypatch):
    conn = policies
    monkeypatch.setenv("DISCOVERY_PROVIDERS", "sitemap")
    cond = _condition(conn)
    c = _campaign(conn, tenants, users, cond)
    c = campaigns.start(conn, tenants["tenant_a"], users["admin"], c["id"])
    _run_discover_job(conn)
    c = campaigns.get(conn, tenants["tenant_a"], c["id"])
    # nothing signed: every page found is parked, the run finishes, and the board says what to do
    assert c["counts"].get("sources_new", 0) == 0 and c["counts"]["sources_pending"] >= 3
    assert c["lifecycle"] == "needs_attention" and "Sources page" in c["next_human_action"]
    assert "start" in c["allowed_actions"]
    nhs_before = {p["domain"]: p["count"] for p in c["pending_publishers"]}["www.nhs.uk"]

    _sign(conn, "www.nhs.uk")
    c = campaigns.start(conn, tenants["tenant_a"], users["admin"], c["id"])
    dispatched = conn.execute(
        "select ci.detail from campaign_item ci where ci.campaign_id=%s and ci.item_table='source_version'", (c["id"],)
    ).fetchall()
    urls = {r["detail"]["url"] for r in dispatched}
    assert "https://www.nhs.uk/conditions/knee-replacement/recovery/" in urls
    assert len([u for u in urls if "nhs.uk" in u]) == nhs_before
    assert all(r["detail"]["via"] == "sitemap" for r in dispatched)  # provenance survives the parking
    c = campaigns.get(conn, tenants["tenant_a"], c["id"])
    assert "www.nhs.uk" not in {p["domain"] for p in c["pending_publishers"]}  # parked items cleared once read
    assert "medlineplus.gov" in {p["domain"] for p in c["pending_publishers"]}  # still waiting
    # the second run's discover job finds nothing new for NHS: already read by this campaign
    _run_discover_job(conn)
    again = conn.execute(
        "select count(*) as n from campaign_item where campaign_id=%s and item_table='source_version'", (c["id"],)
    ).fetchone()["n"]
    assert again == len(urls)


def test_supplied_only_campaigns_do_not_discover(policies, tenants, users, monkeypatch):
    conn = policies
    monkeypatch.setenv("DISCOVERY_PROVIDERS", "sitemap,search")
    cond = _condition(conn)
    c = _campaign(
        conn, tenants, users, cond, source_policy="supplied_only", supplied_source_urls=["https://www.nhs.uk/conditions/knee-replacement/"]
    )
    c = campaigns.start(conn, tenants["tenant_a"], users["admin"], c["id"])
    assert q.claim(conn, "w", stages=("discover",)) is None
    assert conn.execute("select discovery_closed_at from campaign_run where campaign_id=%s", (c["id"],)).fetchone()["discovery_closed_at"]


def test_discovery_off_means_the_job_finds_nothing_and_touches_no_network(policies, tenants, users, monkeypatch):
    conn = policies
    monkeypatch.delenv("DISCOVERY_PROVIDERS", raising=False)  # MOVEAI_ENV=test → nothing

    def boom(*a, **k):
        raise AssertionError("network touched")

    monkeypatch.setattr(discovery, "_get", boom)
    cond = _condition(conn)
    c = _campaign(conn, tenants, users, cond)
    c = campaigns.start(conn, tenants["tenant_a"], users["admin"], c["id"])
    _run_discover_job(conn)
    c = campaigns.get(conn, tenants["tenant_a"], c["id"])
    assert c["lifecycle"] == "needs_attention" and c["counts"].get("sources_pending", 0) == 0
    assert any("found nothing to read" in b for b in c["blockers"])


def test_the_sitemap_byte_ceiling_stops_indexing(policies, offline_sites, tenants, users, monkeypatch):
    conn = policies
    monkeypatch.setenv("DISCOVERY_PROVIDERS", "sitemap")
    cond = _condition(conn)
    c = _campaign(conn, tenants, users, cond, limits={**LIMITS, "max_document_bytes": 10})
    campaigns.start(conn, tenants["tenant_a"], users["admin"], c["id"])
    _run_discover_job(conn)
    ev = conn.execute("select detail from campaign_event where campaign_id=%s and event='discovered'", (c["id"],)).fetchone()["detail"]
    assert all(p["pages_indexed"] == 0 for p in ev["publishers"])
    assert any(p["error"] and "ceiling" in p["error"] for p in ev["publishers"])


def test_naming_the_brave_provider_without_a_key_is_an_error(monkeypatch):
    monkeypatch.setenv("SEARCH_PROVIDER", "brave")
    monkeypatch.delenv("BRAVE_SEARCH_API_KEY", raising=False)
    with pytest.raises(discovery.DiscoveryError, match="BRAVE_SEARCH_API_KEY"):
        discovery.get_search_provider()
