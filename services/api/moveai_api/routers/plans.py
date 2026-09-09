from __future__ import annotations

from typing import Any

import psycopg
from fastapi import APIRouter, Depends, HTTPException

from moveai_contracts.intake import CaseSubmission
from moveai_contracts.plan import ApproveRequest, DraftPatch, PlanOptionsResponse, ReassessmentRequest
from moveai_planner.approval import approve, get_approved, latest_revision, patch_draft, reassess
from moveai_planner.engine import plan_options

from ..auth import Principal, require
from ..db import get_conn
from ..util import as_uuid, clean

router = APIRouter(tags=["plans"])


def _tenant(p: Principal) -> str:
    if not p.tenant_id:
        raise HTTPException(403, {"code": "no_tenant", "message": "patient operations require a tenant context"})
    return p.tenant_id


@router.post("/plan-options", response_model=PlanOptionsResponse)
def create_plan_options(body: CaseSubmission, p: Principal = Depends(require("pt", "clinical_lead", "integration")), conn: psycopg.Connection = Depends(get_conn)) -> PlanOptionsResponse:
    try:
        return plan_options(conn, tenant_id=_tenant(p), user_id=p.user_id, submission=body)
    except ValueError as e:
        raise HTTPException(422, {"code": str(e), "message": str(e)}) from e


@router.get("/plans/{plan_id}")
def get_plan(plan_id: str, p: Principal = Depends(require("pt", "clinical_lead", "auditor")), conn: psycopg.Connection = Depends(get_conn)) -> dict[str, Any]:
    row = latest_revision(conn, _tenant(p), as_uuid(plan_id))
    if not row:
        raise HTTPException(404, {"code": "not_found", "message": "plan not found"})
    return clean(row)  # type: ignore[return-value]


@router.patch("/plans/{plan_id}/draft")
def edit_draft(plan_id: str, body: DraftPatch, p: Principal = Depends(require("pt", "clinical_lead")), conn: psycopg.Connection = Depends(get_conn)) -> dict[str, Any]:
    return clean(patch_draft(conn, tenant_id=_tenant(p), user_id=p.user_id, plan_id=as_uuid(plan_id), patch=body))  # type: ignore[return-value]


@router.post("/plans/{plan_id}/approve")
def approve_plan(plan_id: str, body: ApproveRequest, p: Principal = Depends(require("pt", "clinical_lead")), conn: psycopg.Connection = Depends(get_conn)) -> dict[str, Any]:
    row = approve(conn, tenant_id=_tenant(p), user_id=p.user_id, roles=p.roles, plan_id=as_uuid(plan_id), expected_revision=body.expected_revision, attestation=body.attestation)
    return {"plan_id": plan_id, "revision": row["revision"], "status": row["status"], "approval_signature": row["approval_signature"], "content_hash": row["content_hash"]}


@router.get("/plans/{plan_id}/approved")
def approved_plan(plan_id: str, p: Principal = Depends(require("integration", "pt", "clinical_lead", "auditor")), conn: psycopg.Connection = Depends(get_conn)) -> dict[str, Any]:
    return get_approved(conn, tenant_id=_tenant(p), plan_id=as_uuid(plan_id))


@router.post("/plans/{plan_id}/reassessments", response_model=PlanOptionsResponse)
def create_reassessment(plan_id: str, body: ReassessmentRequest, p: Principal = Depends(require("pt", "clinical_lead")), conn: psycopg.Connection = Depends(get_conn)) -> PlanOptionsResponse:
    cur = latest_revision(conn, _tenant(p), as_uuid(plan_id))
    if not cur:
        raise HTTPException(404, {"code": "not_found", "message": "plan not found"})
    snap = conn.execute("select case_ref from case_snapshot where id=%s", (cur["case_snapshot_id"],)).fetchone()
    sub = CaseSubmission(case_ref=snap["case_ref"], narrative=body.narrative, intake=body.intake, assessment_time=body.assessment_time)
    return reassess(conn, tenant_id=_tenant(p), user_id=p.user_id, plan_id=as_uuid(plan_id), submission=sub)
