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
from moveai_ingestion.source_policies import PolicyError, load_licenses, reject, sign
from pydantic import BaseModel, Field

from ..auth import Principal, current_principal, require
from ..db import get_conn
from ..util import clean

router = APIRouter(tags=["source-policies"])


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
        "terms_excerpt": evidence.get("quoted_span"),
        "terms_fetched_at": evidence.get("fetched_at"),
    }


def _blocked_by(row: dict[str, Any]) -> str | None:
    if row["effective"]:
        return None
    if row["review_state"] == "rejected":
        return f"Rejected: {row['review_note'] or 'no reason recorded'}"
    if row["evidence_state"] == "uncaptured":
        return "Nobody has read this publisher's licence terms yet."
    if row["evidence_state"] == "unreachable":
        return "The licence terms page could not be reached."
    if row["evidence_state"] == "drifted":
        return "This publisher changed its terms after they were signed. They need reading again."
    if row["review_state"] != "signed":
        return "The terms have been read and are waiting for a rights reviewer to accept them."
    if row["can_fetch"] != "allowed":
        return f"The licence does not permit reading: can_fetch is {row['can_fetch']}."
    return "The signature no longer covers the terms on record."


@router.get("/source-policies")
def list_policies(conn: psycopg.Connection = Depends(get_conn), p: Principal = Depends(current_principal)) -> dict[str, Any]:
    licenses = load_licenses()
    rows = conn.execute("select * from source_policy order by effective desc, publisher").fetchall()
    return {
        "items": [_out(r, licenses) for r in rows],
        "readable": sum(1 for r in rows if r["effective"]),
        "total": len(rows),
    }


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
