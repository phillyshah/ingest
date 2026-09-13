"""The curated allowlist, as the reviewer app sees it (spec §4 rights, §5 fetch allowlist).

Read by anyone signed in — knowing which publishers the system may read is not privileged, and a campaign that
returns nothing is much easier to understand when the answer is on a page. Deciding is restricted to the rights
reviewer, which is the same boundary `POST /reviews` already draws for per-source rights decisions.
"""

from __future__ import annotations

from typing import Any

import psycopg
from fastapi import APIRouter, Depends, HTTPException
from moveai_contracts.api import PERMISSION_OPS
from moveai_ingestion.source_policies import PolicyError, capture, load_licenses, reject, sign
from pydantic import BaseModel, Field

from ..auth import Principal, current_principal, require
from ..db import get_conn
from ..util import clean

router = APIRouter(tags=["source-policies"])

# Shorter than the batch script's: this runs inside a request and holds one of the API's pooled database
# connections while it waits on the publisher's server.
ON_DEMAND_CAPTURE_TIMEOUT_S = 12.0


class PolicyDecision(BaseModel):
    decision: str = Field(description="sign or reject")
    note: str | None = None


def _out(row: dict[str, Any], licenses: dict[str, Any]) -> dict[str, Any]:
    lic = licenses.get(row["license_id"])
    evidence = row["evidence"] or {}
    return {
        **clean(row),
        "permissions": {op: row[op] for op in PERMISSION_OPS},
        "license": {
            "id": row["license_id"],
            "name": lic.name if lic else row["license_id"],
            "url": lic.url if lic else None,
            "summary": lic.summary if lic else None,
            "notes": lic.notes if lic else [],
        },
        # What this publisher is waiting for, in one sentence, so the UI does not have to reimplement the rules.
        "blocked_by": _blocked_by(row),
        "status": _status(row),
        "terms_excerpt": evidence.get("quoted_span"),
        "terms_fetched_at": evidence.get("fetched_at"),
        "terms_error": evidence.get("error"),
        "terms_attempted_at": evidence.get("attempted_at"),
    }


def _blocked_by(row: dict[str, Any]) -> str | None:
    if row["effective"]:
        return None
    if row["review_state"] == "rejected":
        return f"Rejected: {row['review_note'] or 'no reason recorded'}"
    if row["evidence_state"] == "uncaptured":
        return "Nobody has read this publisher's licence terms yet."
    if row["evidence_state"] == "unreachable":
        # Say what actually happened. "could not be reached" sent every diagnosis to the same dead end, when a
        # 403 (the site refuses automated clients) and a 404 (our URL is wrong) need different fixes.
        err = (row["evidence"] or {}).get("error")
        return f"Their terms page could not be read — {err}" if err else "The licence terms page could not be reached."
    if row["evidence_state"] == "drifted":
        return "This publisher changed its terms after they were signed. They need reading again."
    if row["review_state"] != "signed":
        return "The terms have been read and are waiting for a rights reviewer to accept them."
    if row["can_fetch"] != "allowed":
        return f"The licence does not permit reading: can_fetch is {row['can_fetch']}."
    return "The signature no longer covers the terms on record."


def _status(row: dict[str, Any]) -> str:
    """One word for where this publisher stands, so the page can group by it instead of re-deriving the rules.

    `accepted_but_unusable` is the case that most needs its own name: the terms were read and accepted, and the
    licence still forbids reading (JOSPT). Filing that under "not readable" alongside "nobody has read the terms"
    makes the list look like a to-do that can never be finished.
    """
    if row["effective"]:
        return "readable"
    if row["review_state"] == "rejected":
        return "rejected"
    if row["review_state"] == "signed" and row["can_fetch"] != "allowed":
        return "accepted_but_unusable"
    if row["evidence_state"] in ("captured", "drifted"):
        return "awaiting_acceptance"
    return "terms_unread"


@router.get("/source-policies")
def list_policies(conn: psycopg.Connection = Depends(get_conn), p: Principal = Depends(current_principal)) -> dict[str, Any]:
    licenses = load_licenses()
    rows = conn.execute("select * from source_policy order by effective desc, publisher").fetchall()
    items = [_out(r, licenses) for r in rows]
    by_status: dict[str, int] = {}
    for i in items:
        by_status[i["status"]] = by_status.get(i["status"], 0) + 1
    return {
        "items": items,
        "readable": sum(1 for r in rows if r["effective"]),
        "total": len(rows),
        "by_status": by_status,
    }


@router.post("/source-policies/{domain}/capture")
def capture_terms_now(
    domain: str,
    p: Principal = Depends(require("rights_reviewer", "clinical_lead", "source_admin")),
    conn: psycopg.Connection = Depends(get_conn),
) -> dict[str, Any]:
    """Read this publisher's terms page now, from the server.

    Until this existed, a publisher whose terms page was momentarily unreachable stayed unacceptable until someone
    ran a GitHub workflow — which is why only a handful of publishers could ever be accepted. Reading the terms is
    not a decision and grants nothing: it fetches the text and records it with its hash. A person still has to
    read it and accept it before the publisher becomes readable.
    """
    try:
        row = capture(conn, domain, timeout=ON_DEMAND_CAPTURE_TIMEOUT_S)
    except PolicyError as e:
        raise HTTPException(404, {"code": "unknown_domain", "message": str(e)}) from e
    return _out(row, load_licenses())


@router.post("/source-policies/{domain}/decision")
def decide(
    domain: str,
    body: PolicyDecision,
    p: Principal = Depends(require("rights_reviewer", "clinical_lead")),
    conn: psycopg.Connection = Depends(get_conn),
) -> dict[str, Any]:
    if body.decision not in ("sign", "reject"):
        raise HTTPException(422, {"code": "bad_decision", "message": "decision must be sign or reject"})
    if body.decision == "reject" and not (body.note or "").strip():
        raise HTTPException(422, {"code": "note_required", "message": "say why this publisher is being rejected"})
    try:
        row = sign(conn, domain, p.user_id, body.note) if body.decision == "sign" else reject(conn, domain, p.user_id, body.note or "")
    except PolicyError as e:
        raise HTTPException(409, {"code": "policy_not_signable", "message": str(e)}) from e
    return _out(row, load_licenses())
