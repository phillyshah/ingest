"""Automatic source discovery for campaigns (spec §21; review milestone M7).

A campaign used to read only the URLs an operator typed plus sources already linked to its condition, which meant
a campaign for a brand-new condition had nothing to read. This module gives it somewhere to look:

    sitemaps   every publisher on the curated allowlist publishes a site index for crawlers (robots.txt →
               sitemap.xml). It is read once per publisher, cached in `publisher_index`/`publisher_page`, and
               each campaign scores the cached URLs against its condition's names.
    search     optionally, a web search API (Brave; a deterministic mock for tests and the demo), bounded by the
               campaign's `max_search_requests`.

What discovery never does is decide that a page may be *read*. Finding a URL in a sitemap or a search result
tells us the page exists; fetching it still requires the publisher's terms to be captured and signed
(`source_policy.effective`), exactly as for a URL the operator typed. Pages on an unsigned publisher, or on a
domain with no policy at all, are parked as pending campaign items with the reason — visible, never fetched.

Reading a publisher's robots.txt and sitemaps before its terms are signed is a deliberate, narrow exception to
"a domain is readable only when signed": those two files exist for exactly this purpose, contain no publisher
content, and reading them is how a campaign can tell an operator "there are 14 pages about knee replacement on
nhs.uk — accept its terms to read them". Rejected publishers are not indexed at all.

Ranking is a heuristic over URL paths and titles (fixtures/discovery/vocabulary.yaml). It only chooses which
pages are worth fetching within the campaign's limits; every fetched page still goes through extraction and PT
review like any other source.
"""

from __future__ import annotations

import gzip
import os
import re
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any, Protocol
from urllib.parse import urljoin, urlparse

import httpx
import psycopg
import yaml
from moveai_contracts.matching import condition_names
from moveai_db import J

from .config import FIXTURES, MAX_DOCUMENT_BYTES
from .fetch import HTTP_HEADERS, FetchError, fetch_http

VOCABULARY_PATH = FIXTURES / "discovery" / "vocabulary.yaml"

INDEX_TTL = timedelta(days=7)  # how long a publisher's sitemap listing is trusted before being re-read
MAX_SITEMAP_FILES = 30  # per publisher per refresh; a sitemap index can list thousands
MAX_URLS_PER_DOMAIN = 200_000
MAX_UNLISTED_SEARCH_RESULTS = 10  # search hits on domains with no policy: recorded for the operator, capped so they do not drown the run
SEARCH_RESULTS_PER_QUERY = 10
USER_AGENT_TOKEN = "moveai-ingest"  # the robots.txt agent name we answer to (matched case-insensitively as a prefix)

_LOC = re.compile(r"<loc>\s*(.*?)\s*</loc>", re.IGNORECASE | re.DOTALL)
_LASTMOD = re.compile(r"<lastmod>\s*(.*?)\s*</lastmod>", re.IGNORECASE | re.DOTALL)
_URL_BLOCK = re.compile(r"<(url|sitemap)>(.*?)</\1>", re.IGNORECASE | re.DOTALL)
_TOKEN = re.compile(r"[a-z0-9]+")


class DiscoveryError(Exception):
    pass


@dataclass
class Candidate:
    url: str
    domain: str
    via: str  # sitemap | search | search_site
    score: int
    matched: list[str] = field(default_factory=list)
    title: str | None = None
    snippet: str | None = None

    def detail(self) -> dict[str, Any]:
        return {"via": self.via, "score": self.score, "matched": self.matched, "title": self.title}


# ------------------------------------------------------------------ terms and scoring
@dataclass(frozen=True)
class Terms:
    phrases: tuple[str, ...]  # multi-word (or long single-word) names, lowercase
    acronyms: tuple[str, ...]  # short names that only count as whole tokens: TKA, THR, ACL


def condition_terms(conditions: list[dict[str, Any]]) -> Terms:
    """The words that make a page about this condition, taken from the condition rows — never invented here."""
    phrases: list[str] = []
    acronyms: list[str] = []
    for c in conditions:
        for n in condition_names(c):
            n = n.strip().lower()
            if not n:
                continue
            if len(n) <= 4 and n.isalnum():
                acronyms.append(n)
            else:
                phrases.append(n)
        # "Total knee arthroplasty, primary" → also "total knee arthroplasty": a qualifier after a comma is
        # catalogue precision, not something a publisher puts in a URL.
        pn = (c.get("preferred_name") or "").lower()
        if "," in pn:
            phrases.append(pn.split(",")[0].strip())
    return Terms(tuple(dict.fromkeys(phrases)), tuple(dict.fromkeys(acronyms)))


def load_vocabulary(path=VOCABULARY_PATH) -> dict[str, list[str]]:
    raw = yaml.safe_load(path.read_text()) or {}
    return {
        "rehab_terms": [str(t).lower() for t in raw.get("rehab_terms", [])],
        "noise_terms": [str(t).lower() for t in raw.get("noise_terms", [])],
    }


def _tokens(text: str) -> list[str]:
    return _TOKEN.findall(text.lower())


def _phrase_in(phrase: str, joined: str) -> bool:
    """`joined` is the token stream joined by single spaces; a phrase matches as a whole-token run."""
    p = " ".join(_tokens(phrase))
    return bool(p) and f" {p} " in f" {joined} "


def score_url(url: str, terms: Terms, vocab: dict[str, list[str]], title: str | None = None) -> tuple[int, list[str]]:
    """How much a URL (and title, when known) looks like a rehabilitation page for the condition.

    Zero unless at least one of the condition's own names appears — the vocabulary can only add to a page that
    is already about the condition, never make an unrelated page a candidate.
    """
    p = urlparse(url)
    path_tokens = _tokens(p.path + " " + (p.query or ""))
    title_tokens = _tokens(title or "")
    joined_path = " ".join(path_tokens)
    joined_all = " ".join(path_tokens + title_tokens)
    matched: list[str] = []
    score = 0
    all_tokens = set(path_tokens) | set(title_tokens)
    for ph in terms.phrases:
        # "knee-replacement" and "kneereplacement" are both how a publisher writes a two-word name in a URL
        if _phrase_in(ph, joined_all) or "".join(_tokens(ph)) in all_tokens:
            score += 3
            matched.append(ph)
    for a in terms.acronyms:
        if a in all_tokens:
            score += 2
            matched.append(a)
    if not matched:
        return 0, []
    for t in vocab.get("rehab_terms", []):
        if _phrase_in(t, joined_all):
            score += 1
            matched.append(t)
    for t in vocab.get("noise_terms", []):
        if _phrase_in(t, joined_path):
            score -= 1
    return max(score, 0), matched


# ------------------------------------------------------------------ robots and sitemaps
def parse_robots(text: str) -> tuple[list[str], list[str]]:
    """(sitemap URLs, disallowed path prefixes that apply to us). Minimal, conservative: the `*` group and any
    group naming our agent both apply; `Allow` lines are ignored, so we err on the side of not fetching."""
    sitemaps: list[str] = []
    disallow: list[str] = []
    applies = False
    for raw in text.splitlines():
        line = raw.split("#", 1)[0].strip()
        if not line or ":" not in line:
            continue
        key, _, value = line.partition(":")
        key, value = key.strip().lower(), value.strip()
        if key == "sitemap":
            if value:
                sitemaps.append(value)
        elif key == "user-agent":
            applies = value == "*" or value.lower().startswith(USER_AGENT_TOKEN)
        elif key == "disallow" and applies and value:
            disallow.append(value)
    return sitemaps, sorted(set(disallow))


def is_disallowed(url: str, disallow: list[str]) -> bool:
    path = urlparse(url).path or "/"
    for d in disallow:
        d = d.rstrip("*")
        if d == "/" or (d and path.startswith(d)):
            return True
    return False


def parse_sitemap(content: bytes) -> tuple[str, list[dict[str, str | None]]]:
    """('index', [{loc}]) or ('urlset', [{loc, lastmod}]). Regex over the two tags we need — no XML parser, so
    entity tricks in an untrusted document have nothing to expand."""
    if content[:2] == b"\x1f\x8b":
        content = gzip.decompress(content[: MAX_DOCUMENT_BYTES * 2])
        if len(content) > MAX_DOCUMENT_BYTES:
            raise DiscoveryError("decompressed sitemap exceeds the document size limit")
    text = content.decode("utf-8", errors="replace")
    kind = "index" if re.search(r"<sitemapindex[\s>]", text, re.IGNORECASE) else "urlset"
    entries: list[dict[str, str | None]] = []
    for m in _URL_BLOCK.finditer(text):
        block = m.group(2)
        loc = _LOC.search(block)
        if not loc:
            continue
        lm = _LASTMOD.search(block)
        entries.append({"loc": _unescape(loc.group(1)), "lastmod": lm.group(1) if lm else None})
    if not entries:  # some sitemaps are a bare list of <loc> without <url> wrappers
        entries = [{"loc": _unescape(m.group(1)), "lastmod": None} for m in _LOC.finditer(text)]
    return kind, entries


def _unescape(s: str) -> str:
    return s.replace("&amp;", "&").replace("&lt;", "<").replace("&gt;", ">").replace("&quot;", '"').replace("&#39;", "'").strip()


def _get(url: str, domain: str) -> bytes | None:
    """One bounded GET through the same fetcher as documents (SSRF, redirects, size). None for any refusal."""
    try:
        return fetch_http(url, extra_domains={domain}).content
    except FetchError:
        return None
    except httpx.HTTPError:
        return None


def refresh_index(conn: psycopg.Connection, domain: str, *, force: bool = False, byte_budget=None) -> dict[str, Any]:
    """Read a publisher's robots.txt and sitemaps into the cache, unless it was read recently.

    `byte_budget(n) -> bool` is asked before each file is counted against a campaign's document-byte ceiling;
    a False answer stops the refresh where it is and records that.
    """
    row = conn.execute("select * from publisher_index where domain=%s", (domain,)).fetchone()
    if row and not force and row["fetched_at"] and datetime.now(UTC) - row["fetched_at"] < INDEX_TTL and row["state"] == "fetched":
        return row
    conn.execute("insert into publisher_index(domain) values (%s) on conflict (domain) do nothing", (domain,))
    base = f"https://{domain}/"
    robots = _get(urljoin(base, "/robots.txt"), domain)
    sitemaps, disallow = parse_robots(robots.decode("utf-8", errors="replace")) if robots else ([], [])
    if not sitemaps:
        sitemaps = [urljoin(base, "/sitemap.xml"), urljoin(base, "/sitemap_index.xml")]
    queue = [s for s in sitemaps if (urlparse(s).hostname or "").lower() == domain]
    read: list[str] = []
    seen_files: set[str] = set()
    total_bytes = 0
    n_urls = 0
    error = None
    now = datetime.now(UTC)
    with conn.cursor() as cur:
        while queue and len(read) < MAX_SITEMAP_FILES and n_urls < MAX_URLS_PER_DOMAIN:
            sm = queue.pop(0)
            if sm in seen_files:
                continue
            seen_files.add(sm)
            content = _get(sm, domain)
            if content is None:
                continue
            if byte_budget and not byte_budget(len(content)):
                error = "stopped: the campaign's document-byte ceiling was reached while reading sitemaps"
                break
            total_bytes += len(content)
            try:
                kind, entries = parse_sitemap(content)
            except DiscoveryError as e:
                error = str(e)
                continue
            read.append(sm)
            if kind == "index":
                queue += [e["loc"] for e in entries if (urlparse(e["loc"]).hostname or "").lower() == domain]
                continue
            rows = [
                (e["loc"], domain, e["lastmod"], now, now)
                for e in entries
                if (urlparse(e["loc"]).hostname or "").lower() == domain and not is_disallowed(e["loc"], disallow)
            ]
            if rows:
                cur.executemany(
                    """insert into publisher_page(url, domain, lastmod, first_seen_at, last_seen_at) values (%s,%s,%s,%s,%s)
                       on conflict (url) do update set lastmod=excluded.lastmod, last_seen_at=excluded.last_seen_at""",
                    rows,
                )
                n_urls += len(rows)
    state = "fetched" if read else ("unreachable" if robots is None else "no_sitemap")
    return conn.execute(
        """update publisher_index set fetched_at=now(), state=%s, sitemap_urls=%s, robots_disallow=%s, url_count=%s, bytes_read=%s, error=%s
            where domain=%s returning *""",
        (state, J(read), J(disallow), n_urls, total_bytes, error, domain),
    ).fetchone()


def _like_patterns(terms: Terms) -> list[str]:
    pats: list[str] = []
    for ph in terms.phrases:
        toks = _tokens(ph)
        if not toks:
            continue
        for sep in ("-", "_", ""):
            pats.append("%" + sep.join(toks) + "%")
    return pats


def sitemap_candidates(conn: psycopg.Connection, domain: str, terms: Terms, vocab: dict[str, list[str]], *, limit: int) -> list[Candidate]:
    """Score the cached pages of one publisher. A SQL prefilter on the condition's names keeps this cheap on a
    six-figure sitemap; the honest scoring happens in Python on what survives."""
    pats = _like_patterns(terms)
    acr = [f"(^|[^a-z0-9]){re.escape(a)}([^a-z0-9]|$)" for a in terms.acronyms]
    if not pats and not acr:
        return []
    rows = conn.execute(
        "select url from publisher_page where domain=%s and (url ilike any(%s) or url ~* any(%s)) limit 5000",
        (domain, pats, acr),
    ).fetchall()
    out: list[Candidate] = []
    for r in rows:
        score, matched = score_url(r["url"], terms, vocab)
        if score > 0:
            out.append(Candidate(url=r["url"], domain=domain, via="sitemap", score=score, matched=matched))
    out.sort(key=lambda c: (-c.score, c.url))
    return out[:limit]


# ------------------------------------------------------------------ web search
class SearchProvider(Protocol):
    name: str

    def search(self, query: str, *, count: int) -> list[dict[str, Any]]:  # [{url, title, snippet}]
        ...


class NoSearch:
    name = "none"

    def search(self, query: str, *, count: int) -> list[dict[str, Any]]:
        return []


class MockSearch:
    """Deterministic results for tests and the demo. `results` maps a query substring to hits."""

    name = "mock"
    results: dict[str, list[dict[str, Any]]] = {}

    def search(self, query: str, *, count: int) -> list[dict[str, Any]]:
        q = query.lower()
        hits = [h for k, v in self.results.items() if k.lower() in q for h in v]
        return hits[:count]


class BraveSearch:
    """Brave Web Search API. The key is read from the environment; a query is one request and counts as one
    against the campaign's search-request ceiling."""

    name = "brave"
    endpoint = "https://api.search.brave.com/res/v1/web/search"

    def __init__(self, api_key: str):
        self.api_key = api_key

    def search(self, query: str, *, count: int) -> list[dict[str, Any]]:
        with httpx.Client(timeout=20, headers={**HTTP_HEADERS, "Accept": "application/json", "X-Subscription-Token": self.api_key}) as c:
            r = c.get(self.endpoint, params={"q": query, "count": min(count, 20), "safesearch": "moderate", "text_decorations": "false"})
        if r.status_code != 200:
            raise DiscoveryError(f"search provider returned HTTP {r.status_code}")
        data = r.json()
        out = []
        for hit in (data.get("web") or {}).get("results") or []:
            if hit.get("url"):
                out.append({"url": hit["url"], "title": hit.get("title"), "snippet": hit.get("description")})
        return out


def get_search_provider() -> SearchProvider:
    """`SEARCH_PROVIDER`: brave | mock | none. Unset: brave when a key is present, else none. Naming brave without
    a key raises rather than silently searching nothing."""
    name = os.environ.get("SEARCH_PROVIDER", "").strip().lower()
    key = os.environ.get("BRAVE_SEARCH_API_KEY", "").strip()
    if not name:
        name = "brave" if key else "none"
    if name == "none":
        return NoSearch()
    if name == "mock":
        return MockSearch()
    if name == "brave":
        if not key:
            raise DiscoveryError("SEARCH_PROVIDER=brave but BRAVE_SEARCH_API_KEY is not set")
        return BraveSearch(key)
    raise DiscoveryError(f"unknown SEARCH_PROVIDER {name!r}")


def discovery_providers() -> set[str]:
    """`DISCOVERY_PROVIDERS`: comma-separated subset of sitemap,search (or `none`).

    Unset: both in a deployed environment (MOVEAI_ENV staging/production — an existing .env need not change to get
    discovery), nothing anywhere else, so the test suite and CI never touch the network.
    """
    raw = os.environ.get("DISCOVERY_PROVIDERS")
    if raw is None:
        return {"sitemap", "search"} if os.environ.get("MOVEAI_ENV") in ("staging", "production") else set()
    return {p.strip() for p in raw.lower().split(",") if p.strip()} - {"none"}


def search_queries(conditions: list[dict[str, Any]], effective_domains: list[str]) -> list[tuple[str, str]]:
    """(query, via). Plain-language names first — a publisher writes "knee replacement", not "arthroplasty"."""
    out: list[tuple[str, str]] = []
    for c in conditions:
        # preferred name and synonyms only: the internal code ("mcl_tear_surgical_repair") is ours, not a phrase
        # anyone searches for
        names = [str(n) for n in (c.get("synonyms") or []) if len(str(n)) > 4]
        pn = (c.get("preferred_name") or "").split(",")[0].strip()
        primary = [pn] + [n for n in names if n.lower() != pn.lower()][:2]
        for n in primary:
            out.append((f"{n} exercises", "search"))
            out.append((f"{n} rehabilitation protocol", "search"))
        for d in effective_domains:
            out.append((f"{primary[0]} exercises site:{d}", "search_site"))
    return list(dict.fromkeys(out))


# ------------------------------------------------------------------ the discovery pass
def discover(
    conn: psycopg.Connection,
    *,
    run_id: Any,
    conditions: list[dict[str, Any]],
    limits: dict[str, Any],
    providers: set[str] | None = None,
    search: SearchProvider | None = None,
) -> dict[str, Any]:
    """Find candidate pages for the scoped conditions within the campaign's limits.

    Returns {candidates: [Candidate], summary: {...}}. Never fetches a candidate: dispatching (and the policy
    gate that parks unsigned publishers) is the caller's job.
    """
    providers = discovery_providers() if providers is None else providers
    terms = condition_terms(conditions)
    vocab = load_vocabulary()
    max_candidates = int(limits.get("max_candidates", 40))
    policies = conn.execute(
        "select domain, publisher, effective, review_state from source_policy where review_state <> 'rejected' order by effective desc, domain"
    ).fetchall()
    summary: dict[str, Any] = {
        "providers": sorted(providers),
        "terms": {"phrases": list(terms.phrases), "acronyms": list(terms.acronyms)},
        "publishers": [],
        "search": [],
    }
    candidates: list[Candidate] = []

    def bytes_ok(n: int) -> bool:
        return conn.execute("select reserve_budget(%s,null,0::numeric,0,%s::bigint) as ok", (run_id, n)).fetchone()["ok"]

    if "sitemap" in providers and terms.phrases + terms.acronyms:
        for pol in policies:
            if len(candidates) >= max_candidates:
                break
            idx = refresh_index(conn, pol["domain"], byte_budget=bytes_ok)
            found = sitemap_candidates(conn, pol["domain"], terms, vocab, limit=max_candidates)
            candidates += found
            summary["publishers"].append(
                {
                    "domain": pol["domain"],
                    "publisher": pol["publisher"],
                    "readable": bool(pol["effective"]),
                    "index_state": idx["state"],
                    "pages_indexed": idx["url_count"],
                    "matches": len(found),
                    "error": idx["error"],
                }
            )

    if "search" in providers:
        search = search or get_search_provider()
        if search.name != "none":
            effective = [p["domain"] for p in policies if p["effective"]]
            listed = {p["domain"] for p in policies}
            unlisted_hits = 0
            for query, via in search_queries(conditions, effective):
                if len(candidates) >= max_candidates:
                    break
                if not conn.execute("select reserve_budget(%s,null,0::numeric,1,0::bigint) as ok", (run_id,)).fetchone()["ok"]:
                    summary["search"].append({"query": query, "skipped": "max_search_requests reached"})
                    break
                try:
                    hits = search.search(query, count=SEARCH_RESULTS_PER_QUERY)
                except DiscoveryError as e:
                    summary["search"].append({"query": query, "error": str(e)})
                    continue
                kept = 0
                for h in hits:
                    host = (urlparse(h["url"]).hostname or "").lower()
                    if not host:
                        continue
                    score, matched = score_url(h["url"], terms, vocab, title=h.get("title"))
                    if score <= 0 and host in listed:
                        continue  # a listed publisher's page that does not mention the condition is not a candidate
                    if host not in listed:
                        if unlisted_hits >= MAX_UNLISTED_SEARCH_RESULTS or score <= 0:
                            continue
                        unlisted_hits += 1
                    candidates.append(
                        Candidate(
                            url=h["url"], domain=host, via=via, score=score, matched=matched, title=h.get("title"), snippet=h.get("snippet")
                        )
                    )
                    kept += 1
                summary["search"].append({"query": query, "provider": search.name, "hits": len(hits), "kept": kept})

    # Dedupe, best score first, within the ceiling. Readable publishers first among equals so the run's
    # max_sources goes to pages that can actually be fetched.
    readable = {p["domain"] for p in policies if p["effective"]}
    best: dict[str, Candidate] = {}
    for c in candidates:
        if c.url not in best or c.score > best[c.url].score:
            best[c.url] = c
    ordered = sorted(best.values(), key=lambda c: (c.domain not in readable, -c.score, c.url))[:max_candidates]
    summary["candidates"] = len(ordered)
    summary["readable_candidates"] = sum(1 for c in ordered if c.domain in readable)
    return {"candidates": ordered, "summary": summary}
