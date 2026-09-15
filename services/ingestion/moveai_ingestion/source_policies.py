"""The curated allowlist: licence catalogue, publisher policies, and the rights grants derived from them.

Spec §4 (rights), §5 (fetch allowlist), §21 (directed campaigns).

The point of this module is that a campaign can be told "find sources about frozen shoulder" and have somewhere
to look, without that becoming a licence to crawl. Three things stay strictly separated:

    the licence      what the terms permit, mapped to the twelve operations     fixtures/source-policies/licenses.yaml
    the policy       which publisher claims which licence, and where it says so fixtures/source-policies/publishers.yaml
    the signature    a rights reviewer read the captured terms and accepted them  source_policy.review_state

Nothing here can make a domain fetchable on its own. `source_policy.effective` is a generated column in the
database, and it requires a signature over the exact terms text on record. Code cannot route around it.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import psycopg
import yaml
from moveai_contracts.api import PERMISSION_OPS
from moveai_db import J

from .config import ROOT

POLICY_DIR = ROOT / "fixtures" / "source-policies"

# Permissiveness order. A publisher override may move a permission down this list but never up: the licence is a
# ceiling, and a per-publisher note is not a grant of rights the licence does not give.
RANK = {"denied": 0, "unknown": 1, "allowed": 2}


class PolicyError(Exception):
    """A policy file is malformed, or claims something it is not allowed to claim."""


@dataclass(frozen=True)
class License:
    id: str
    name: str
    url: str | None
    summary: str
    attribution_required: bool | None
    permissions: dict[str, str]
    notes: list[str]


@dataclass(frozen=True)
class Policy:
    domain: str
    publisher: str
    license_id: str
    policy_reference: str
    scope_note: str | None
    permissions: dict[str, str]
    attribution_required: bool | None


# ------------------------------------------------------------------ loading
def load_licenses(path: Path | None = None) -> dict[str, License]:
    raw = yaml.safe_load((path or POLICY_DIR / "licenses.yaml").read_text())
    out: dict[str, License] = {}
    for entry in raw["licenses"]:
        perms = entry.get("permissions") or {}
        missing = [op for op in PERMISSION_OPS if op not in perms]
        if missing:
            raise PolicyError(f"licence {entry['id']!r} does not say anything about: {', '.join(missing)}")
        unknown_ops = [op for op in perms if op not in PERMISSION_OPS]
        if unknown_ops:
            raise PolicyError(f"licence {entry['id']!r} names operations that do not exist: {', '.join(unknown_ops)}")
        bad = [f"{op}={v}" for op, v in perms.items() if v not in RANK]
        if bad:
            raise PolicyError(f"licence {entry['id']!r} has values that are not allowed|denied|unknown: {', '.join(bad)}")
        if entry["id"] in out:
            raise PolicyError(f"duplicate licence id {entry['id']!r}")
        out[entry["id"]] = License(
            id=entry["id"],
            name=entry["name"],
            url=entry.get("url"),
            summary=entry.get("summary", "").strip(),
            attribution_required=entry.get("attribution_required"),
            permissions=dict(perms),
            notes=list(entry.get("notes") or []),
        )
    return out


def load_publishers(path: Path | None = None, licenses: dict[str, License] | None = None) -> list[Policy]:
    licenses = licenses if licenses is not None else load_licenses()
    raw = yaml.safe_load((path or POLICY_DIR / "publishers.yaml").read_text())
    out: list[Policy] = []
    seen: set[str] = set()
    for entry in raw["publishers"]:
        domain = str(entry["domain"]).strip().lower()
        if domain != entry["domain"]:
            raise PolicyError(f"domain {entry['domain']!r} must be written lowercase and unpadded")
        if "/" in domain or ":" in domain or "*" in domain:
            raise PolicyError(f"domain {domain!r} must be a bare host: no scheme, port, path or wildcard")
        if domain in seen:
            raise PolicyError(f"duplicate domain {domain!r}")
        seen.add(domain)

        lic = licenses.get(entry["license_id"])
        if lic is None:
            raise PolicyError(f"{domain}: unknown licence {entry['license_id']!r}")

        ref = entry["policy_reference"]
        if urlparse(ref).scheme != "https":
            raise PolicyError(f"{domain}: policy_reference must be an https URL, got {ref!r}")
        # The terms that govern a domain have to come from that domain. A policy citing someone else's page is
        # how a plausible-looking allowlist entry ends up resting on nothing.
        if (urlparse(ref).hostname or "").lower() != domain:
            raise PolicyError(f"{domain}: policy_reference must be served by the domain it governs, got {ref!r}")

        perms = dict(lic.permissions)
        for op, value in (entry.get("overrides") or {}).items():
            if op not in PERMISSION_OPS:
                raise PolicyError(f"{domain}: override names an operation that does not exist: {op}")
            if value not in RANK:
                raise PolicyError(f"{domain}: override {op}={value!r} is not allowed|denied|unknown")
            if RANK[value] > RANK[perms[op]]:
                raise PolicyError(
                    f"{domain}: override {op}={value!r} is more permissive than licence {lic.id} "
                    f"({perms[op]!r}). A publisher note can narrow a licence, never widen it."
                )
            perms[op] = value

        out.append(
            Policy(
                domain=domain,
                publisher=entry["publisher"],
                license_id=lic.id,
                policy_reference=ref,
                scope_note=(entry.get("scope_note") or "").strip() or None,
                permissions=perms,
                attribution_required=lic.attribution_required,
            )
        )
    return out


# ------------------------------------------------------------------ database
_PERM_COLS = ", ".join(PERMISSION_OPS)


def sync(conn: psycopg.Connection, policies: list[Policy] | None = None) -> dict[str, Any]:
    """Bring source_policy in line with the files. Signatures survive unless something they cover changed.

    Never deletes. A row in the database with no file behind it is reported, not removed: it may have been added
    by hand for a negotiated agreement, and silently dropping a rights record is not a thing this should do.
    """
    policies = policies if policies is not None else load_publishers()
    added, updated, unchanged = [], [], []

    for p in policies:
        existing = conn.execute("select * from source_policy where domain=%s", (p.domain,)).fetchone()
        if existing is None:
            conn.execute(
                f"""insert into source_policy(domain, publisher, license_id, policy_reference, scope_note,
                        {_PERM_COLS}, attribution_required)
                    values (%s,%s,%s,%s,%s,{",".join(["%s"] * len(PERMISSION_OPS))},%s)""",
                (
                    p.domain,
                    p.publisher,
                    p.license_id,
                    p.policy_reference,
                    p.scope_note,
                    *[p.permissions[op] for op in PERMISSION_OPS],
                    p.attribution_required,
                ),
            )
            added.append(p.domain)
            continue

        same = (
            existing["license_id"] == p.license_id
            and existing["policy_reference"] == p.policy_reference
            and existing["publisher"] == p.publisher
            and existing["scope_note"] == p.scope_note
            and all(existing[op] == p.permissions[op] for op in PERMISSION_OPS)
        )
        if same:
            unchanged.append(p.domain)
            continue
        sets = ", ".join(
            ["publisher=%s", "license_id=%s", "policy_reference=%s", "scope_note=%s"]
            + [f"{op}=%s" for op in PERMISSION_OPS]
            + ["attribution_required=%s"]
        )
        conn.execute(
            f"update source_policy set {sets} where domain=%s",
            (
                p.publisher,
                p.license_id,
                p.policy_reference,
                p.scope_note,
                *[p.permissions[op] for op in PERMISSION_OPS],
                p.attribution_required,
                p.domain,
            ),
        )
        updated.append(p.domain)

    known = {p.domain for p in policies}
    orphaned = [r["domain"] for r in conn.execute("select domain from source_policy").fetchall() if r["domain"] not in known]
    return {"added": added, "updated": updated, "unchanged": unchanged, "orphaned": orphaned}


def policy_for_url(conn: psycopg.Connection, url: str) -> dict[str, Any] | None:
    """The policy governing a URL, whatever its state. Exact host match only.

    No parent-domain fallback on purpose: a policy signed for `www.nhs.uk` says nothing about what some other
    subdomain publishes, and inheriting rights down a domain tree is how an allowlist stops meaning anything.
    """
    host = (urlparse(url).hostname or "").lower()
    if not host:
        return None
    return conn.execute("select * from source_policy where domain=%s", (host,)).fetchone()


def effective_domains(conn: psycopg.Connection) -> set[str]:
    """Domains the fetcher may reach. Signed, signature still covering the terms on record, and can_fetch allowed."""
    return {r["domain"] for r in conn.execute("select domain from source_policy where effective").fetchall()}


def materialise_rights_grant(conn: psycopg.Connection, source_version_id: Any, url: str) -> dict[str, Any] | None:
    """Create the rights grant for a source version from its domain policy.

    Returns None when no policy governs the domain, which leaves the source version with no grant at all — and a
    missing grant already blocks every operation, so the absence needs no special handling downstream.

    An unsigned or drifted policy still produces a grant, carrying its permissions. They will be `unknown` or
    worse, so it blocks; recording it anyway means the reviewer sees *why* rather than a blank.
    """
    pol = policy_for_url(conn, url)
    if pol is None:
        return None
    evidence = {
        "kind": "domain_policy",
        "policy_id": str(pol["id"]),
        "domain": pol["domain"],
        "license_id": pol["license_id"],
        "url": pol["policy_reference"],
        "terms_sha256": (pol["evidence"] or {}).get("sha256"),
        "terms_captured_at": (pol["evidence"] or {}).get("fetched_at"),
        "signed": pol["review_state"] == "signed",
        "effective": pol["effective"],
        "scope_note": pol["scope_note"],
    }
    # An unsigned policy grants nothing, whatever its licence says. Materialising its stated permissions would
    # make a grant that reads as permission when no person has accepted the terms.
    perms = {op: (pol[op] if pol["effective"] else "unknown") for op in PERMISSION_OPS}
    perms["can_train_model"] = "denied"
    return conn.execute(
        f"""insert into rights_grant(source_version_id, source_policy_id, {_PERM_COLS},
                attribution_required, attribution_text, territory, permission_evidence)
            values (%s,%s,{",".join(["%s"] * len(PERMISSION_OPS))},%s,%s,%s,%s) returning *""",
        (
            source_version_id,
            pol["id"],
            *[perms[op] for op in PERMISSION_OPS],
            pol["attribution_required"],
            pol["attribution_text"],
            pol["territory"],
            J(evidence),
        ),
    ).fetchone()


# ------------------------------------------------------------------ reading a publisher's terms
# Lives here rather than in scripts/source_policies.py so the reviewer app can offer "read their terms now" as a
# button. Nothing about the rule changes: this only fetches and records the text; a person still has to read it
# and accept it before the publisher becomes readable.
CAPTURE_TIMEOUT_S = 30
MAX_TERMS_BYTES = 4 * 1024 * 1024
QUOTE_CHARS = 600
MIN_TERMS_CHARS = 200

# Identify honestly and send the headers any well-formed HTTP client sends. Several publishers returned 403 to a
# bare User-Agent with no Accept headers. This is not a disguise: the agent string still says what this is and why
# it is here. If a publisher still refuses, that is their answer and it gets recorded as one — we do not pretend to
# be a browser to get around it.
CAPTURE_HEADERS = {
    "User-Agent": "MoveAI-Ingest/0.1 (licence terms verification; allowlist only)",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-GB,en;q=0.9",
}

_HTTP_MEANING = {
    403: "the site refuses automated clients. A different terms URL may work; otherwise this publisher cannot be verified this way.",
    404: "no such page — the terms URL on record is wrong and needs correcting in the publisher list.",
    429: "rate limited; try again in a few minutes.",
    503: "the site is temporarily unavailable; try again shortly.",
}


def terms_text(content: bytes) -> str:
    """The licence terms as text, normalised so that presentation changes are not read as licence changes.

    Hashing the raw bytes would flag a new cookie banner or a rotated analytics tag as a change of terms, and a
    re-verification prompt that cries wolf is one nobody reads. Hashing extracted, whitespace-normalised text
    means the hash moves when the words move.
    """
    import re

    from .parse import parse_html

    return re.sub(r"\s+", " ", parse_html(content).text()).strip()


def capture_terms(policy_reference: str, client: Any = None, timeout: float = CAPTURE_TIMEOUT_S) -> dict[str, Any]:
    """Fetch one publisher's terms page. Returns {state, evidence}; never raises for a bad site.

    `timeout` is shorter when this runs inside a web request than in the batch script: a request-served capture
    is holding one of the API's few pooled database connections while it waits on someone else's server.
    """
    import httpx

    own = client is None
    if own:
        client = httpx.Client(follow_redirects=True, timeout=timeout, headers=CAPTURE_HEADERS)
    try:
        try:
            resp = client.get(policy_reference)
        except httpx.HTTPError as e:
            return {
                "state": "unreachable",
                "evidence": {"error": f"{type(e).__name__}: {e}", "attempted_at": datetime.now(UTC).isoformat()},
            }
        if resp.status_code != 200:
            # Name the status. The first real run reported these as "0 chars", which reads like an empty page and
            # sent the diagnosis straight past the actual cause — a 403 (the site refuses unknown clients) and a
            # 404 (the URL is simply wrong) need completely different fixes, and neither is "0 chars".
            meaning = _HTTP_MEANING.get(resp.status_code, "")
            return {
                "state": "unreachable",
                "evidence": {
                    "http_status": resp.status_code,
                    "final_url": str(resp.url),
                    "error": f"HTTP {resp.status_code}" + (f" — {meaning}" if meaning else ""),
                    "attempted_at": datetime.now(UTC).isoformat(),
                },
            }
        text = terms_text(resp.content[:MAX_TERMS_BYTES])
        if len(text) < MIN_TERMS_CHARS:
            # A terms page that extracts to nothing is almost always a JavaScript shell or a consent interstitial.
            # Signing that would be signing a blank page.
            return {
                "state": "unreachable",
                "evidence": {
                    "http_status": resp.status_code,
                    "final_url": str(resp.url),
                    "error": f"only {len(text)} characters of text; the page is probably rendered client-side",
                    "attempted_at": datetime.now(UTC).isoformat(),
                },
            }
        return {
            "state": "captured",
            "evidence": {
                "sha256": hashlib.sha256(text.encode()).hexdigest(),
                "fetched_at": datetime.now(UTC).isoformat(),
                "http_status": resp.status_code,
                "final_url": str(resp.url),
                "text_chars": len(text),
                "quoted_span": text[:QUOTE_CHARS],
            },
        }
    finally:
        if own:
            client.close()


def record_capture(conn: psycopg.Connection, pol: dict[str, Any], out: dict[str, Any]) -> dict[str, Any]:
    """Store a capture result against a policy, marking drift when the text no longer matches what was signed."""
    state = out["state"]
    new_sha = out["evidence"].get("sha256")
    # Drift is not just "different from last time" — it is "different from what someone signed". That is the
    # comparison that matters, and the one that must be loud.
    if state == "captured" and pol["signed_evidence_sha256"] and new_sha != pol["signed_evidence_sha256"]:
        state = "drifted"
    return conn.execute(
        "update source_policy set evidence=%s, evidence_state=%s where id=%s returning *",
        (J(out["evidence"]), state, pol["id"]),
    ).fetchone()


def capture(conn: psycopg.Connection, domain: str, client: Any = None, timeout: float = CAPTURE_TIMEOUT_S) -> dict[str, Any]:
    """Read one publisher's terms page now and record what came back."""
    pol = conn.execute("select * from source_policy where domain=%s", (domain.lower(),)).fetchone()
    if pol is None:
        raise PolicyError(f"no policy for domain {domain!r}")
    return record_capture(conn, pol, capture_terms(pol["policy_reference"], client, timeout))


def sign(conn: psycopg.Connection, domain: str, reviewer_id: Any, note: str | None = None) -> dict[str, Any]:
    """Record that a rights reviewer read the captured terms and accepted them.

    The signature is over the evidence hash, so it cannot be given in advance of the terms being fetched, and it
    stops meaning anything the moment the publisher changes them.
    """
    pol = conn.execute("select * from source_policy where domain=%s", (domain.lower(),)).fetchone()
    if pol is None:
        raise PolicyError(f"no policy for domain {domain!r}")
    sha = (pol["evidence"] or {}).get("sha256")
    if not sha:
        raise PolicyError(
            f"{domain}: the terms at {pol['policy_reference']} have not been fetched yet, so there is nothing to sign. "
            "Run the source policy evidence capture first."
        )
    if pol["evidence_state"] == "unreachable":
        raise PolicyError(f"{domain}: the last attempt to read {pol['policy_reference']} failed; re-capture before signing")
    return conn.execute(
        """update source_policy
              set review_state='signed', reviewed_by=%s, reviewed_at=now(), review_note=%s,
                  signed_evidence_sha256=%s, evidence_state='captured',
                  next_review_at = now() + make_interval(days => review_cadence_days)
            where domain=%s returning *""",
        (reviewer_id, note, sha, pol["domain"]),
    ).fetchone()


def reject(conn: psycopg.Connection, domain: str, reviewer_id: Any, note: str) -> dict[str, Any]:
    """Record a decision not to use a publisher, with the reason. Keeps it off the list without losing the answer."""
    row = conn.execute(
        """update source_policy set review_state='rejected', reviewed_by=%s, reviewed_at=now(), review_note=%s,
               signed_evidence_sha256=null where domain=%s returning *""",
        (reviewer_id, note, domain.lower()),
    ).fetchone()
    if row is None:
        raise PolicyError(f"no policy for domain {domain!r}")
    return row
