"""Directed ingestion campaigns (spec §21): scope resolution, gap analysis, bounded runs, control actions,
lifecycle derived from real job/coverage state, and reconciled progress counters."""
from __future__ import annotations

import uuid
from datetime import date, datetime, timedelta, timezone
from typing import Any

import psycopg

from moveai_db import J

from . import queue as q
from .fetch import canonicalize
from .terminology import resolve_code

HEARTBEAT_STALE_S = 60
EST_COST_PER_SOURCE_USD = 0.05   # reservation estimate; settled with actual model cost


class CampaignError(Exception):
    def __init__(self, code: str, message: str, status: int = 409):
        super().__init__(f"{code}: {message}")
        self.code, self.message, self.status = code, message, status


# ------------------------------------------------------------ scope
def match_conditions(conn: psycopg.Connection, text: str | None) -> list[dict[str, Any]]:
    if not text:
        return []
    low = text.lower()
    out = []
    for c in conn.execute("select * from condition").fetchall():
        names = [c["preferred_name"], c["internal_code"].replace("_", " "), *c["synonyms"]]
        if any(n and n.lower() in low for n in names):
            out.append(c)
    return out


def conditions_for_codes(conn: psycopg.Connection, codes: list[str]) -> list[dict[str, Any]]:
    if not codes:
        return []
    return conn.execute(
        """select distinct c.* from condition c join diagnosis_mapping_version m on m.condition_id=c.id
             join terminology_code tc on tc.id=m.terminology_code_id where tc.code = any(%s)""", ([c.upper() for c in codes],)).fetchall()


def scope_preview(conn: psycopg.Connection, tenant_id: Any, scope: dict[str, Any]) -> dict[str, Any]:
    svc_date = date.fromisoformat(scope["service_date"]) if scope.get("service_date") else date.today()
    resolved = [resolve_code(conn, c, svc_date) for c in scope.get("codes", [])]
    conds = {c["id"]: c for c in match_conditions(conn, scope.get("ailment_text"))}
    for c in conditions_for_codes(conn, [r["code"] for r in resolved if r["resolved"]]):
        conds[c["id"]] = c
    unresolved_text = []
    if scope.get("ailment_text") and not match_conditions(conn, scope.get("ailment_text")):
        unresolved_text.append(scope["ailment_text"])
    blocking: list[str] = []
    if not conds:
        blocking.append("no condition could be interpreted from the ailment text or codes")
    if any(not r["resolved"] for r in resolved):
        blocking.append("one or more codes did not resolve against the effective terminology release")
    if len(conds) > 1:
        blocking.append(f"ambiguous scope: {len(conds)} conditions matched; narrow the request before activation")
    coverage: dict[str, Any] = {}
    missing: list[str] = []
    for c in conds.values():
        protos = conn.execute("select id, name, approval_state, phases from protocol_version where condition_id=%s and approval_state not in ('rejected','withdrawn','superseded','invalidated')", (c["id"],)).fetchall()
        uses = conn.execute("select approval_state, count(*) as n from clinical_use_version where condition_id=%s group by approval_state", (c["id"],)).fetchall()
        approved_protos = [p for p in protos if p["approval_state"] in ("approved", "published")]
        coverage[c["internal_code"]] = {"protocols": len(protos), "approved_protocols": len(approved_protos),
                                        "clinical_uses": {u["approval_state"]: u["n"] for u in uses},
                                        "phases_defined": sorted({ph["name"] for p in protos for ph in p["phases"]})}
        if not approved_protos:
            missing.append(f"{c['preferred_name']}: no approved pathway; placeholder structure only")
        for p in protos:
            for ph in p["phases"]:
                if ph.get("gaps"):
                    missing.append(f"{c['preferred_name']} / {ph['name']}: {', '.join(ph['gaps'])}")
    sources = conn.execute("select canonical_url, publisher, allowlist_state from source where allowlist_state='approved' order by created_at").fetchall()
    strategy = [f"supplied source: {u}" for u in scope.get("supplied_source_urls", [])]
    if scope.get("source_policy") != "supplied_only":
        strategy += [f"allowlisted source: {s['canonical_url']}" for s in sources[:20]]
    strategy.append("no new-domain discovery: domains outside the allowlist are recorded as pending, never fetched")
    return {"interpreted_conditions": [{"id": str(c["id"]), "code": c["internal_code"], "name": c["preferred_name"], "ambiguity_flags": c["ambiguity_flags"]} for c in conds.values()],
            "resolved_codes": resolved, "unresolved_text": unresolved_text,
            "included": {"conditions": [c["preferred_name"] for c in conds.values()], "refinements": scope.get("refinements", {})},
            "excluded": scope.get("exclusion", {}), "existing_coverage": coverage, "missing": sorted(set(missing)),
            "proposed_strategy": strategy, "max_work": scope["limits"],
            "clinical_review_requirements": ["every extracted variant and clinical use requires PT review", "protocol publication requires clinical lead approval",
                                             "rights reviewer clears any graphic before patient display"],
            "can_start": not blocking and bool(scope.get("scope_confirmed")), "blocking_reasons": blocking + ([] if scope.get("scope_confirmed") else ["scope not confirmed"])}


# ------------------------------------------------------------ create / revise
def create(conn: psycopg.Connection, tenant_id: Any, user_id: Any, body: dict[str, Any]) -> dict[str, Any]:
    scope = body["scope"]
    camp = conn.execute("insert into ingestion_campaign(tenant_id, owner_id, reviewer_id, title, priority) values (%s,%s,%s,%s,%s) returning *",
                        (tenant_id, body.get("owner_id") or user_id, body.get("reviewer_id"), body["title"], scope.get("priority", 100))).fetchone()
    sv = _scope_version(conn, camp["id"], 1, scope, user_id)
    conn.execute("update ingestion_campaign set current_scope_version_id=%s where id=%s", (sv["id"], camp["id"]))
    _event(conn, camp["id"], None, "created", user_id, {"title": body["title"]})
    return get(conn, tenant_id, camp["id"])


def _scope_version(conn: psycopg.Connection, campaign_id: Any, version: int, scope: dict[str, Any], user_id: Any) -> dict:
    preview = scope_preview(conn, None, scope)
    return conn.execute(
        """insert into campaign_scope_version(campaign_id, version, ailment_text, codes, condition_ids, inclusion, exclusion, refinements, supplied_source_urls,
             desired_output, source_policy, limits, acceptance_criteria, media_policy, authorized_by, authorized_at)
           values (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) returning *""",
        (campaign_id, version, scope.get("ailment_text"), J(preview["resolved_codes"]), [uuid.UUID(c["id"]) for c in preview["interpreted_conditions"]],
         J(preview["included"]), J(scope.get("exclusion", {})), J(scope.get("refinements", {})), scope.get("supplied_source_urls", []),
         scope.get("desired_output", "text_first_phased_plan_templates"), scope.get("source_policy", "allowlist_only"), J(scope["limits"]),
         J(scope.get("acceptance_criteria", [])), scope.get("media_policy", "reference_only"),
         user_id if scope.get("scope_confirmed") else None, "now()" if scope.get("scope_confirmed") else None)).fetchone()


def revise_scope(conn: psycopg.Connection, tenant_id: Any, user_id: Any, campaign_id: Any, scope: dict[str, Any]) -> dict[str, Any]:
    camp = _camp(conn, tenant_id, campaign_id)
    n = conn.execute("select coalesce(max(version),0) as v from campaign_scope_version where campaign_id=%s", (camp["id"],)).fetchone()["v"]
    sv = _scope_version(conn, camp["id"], n + 1, scope, user_id)
    conn.execute("update ingestion_campaign set current_scope_version_id=%s, priority=%s where id=%s", (sv["id"], scope.get("priority", camp["priority"]), camp["id"]))
    _event(conn, camp["id"], None, "scope_revised", user_id, {"version": n + 1, "active_run_affected": bool(_active_run(conn, camp["id"]))})
    return get(conn, tenant_id, camp["id"])


# ------------------------------------------------------------ runs & controls
def _camp(conn: psycopg.Connection, tenant_id: Any, campaign_id: Any) -> dict:
    row = conn.execute("select * from ingestion_campaign where id=%s and tenant_id=%s", (campaign_id, tenant_id)).fetchone()
    if not row:
        raise CampaignError("not_found", "campaign not found", 404)
    return row


def _active_run(conn: psycopg.Connection, campaign_id: Any) -> dict | None:
    return conn.execute("select * from campaign_run where campaign_id=%s and state in ('pending','running','paused') order by run_number desc limit 1", (campaign_id,)).fetchone()


def _event(conn: psycopg.Connection, campaign_id: Any, run_id: Any, event: str, actor: Any, detail: dict | None = None) -> None:
    conn.execute("insert into campaign_event(campaign_id, run_id, event, actor_id, detail) values (%s,%s,%s,%s,%s)", (campaign_id, run_id, event, actor, J(detail or {})))


def start(conn: psycopg.Connection, tenant_id: Any, user_id: Any, campaign_id: Any) -> dict[str, Any]:
    camp = _camp(conn, tenant_id, campaign_id)
    if camp["control"] == "cancelled":
        raise CampaignError("cancelled", "cancelled campaigns cannot start; propose a new run from it instead")
    if _active_run(conn, camp["id"]):
        raise CampaignError("already_running", "a run is already active")
    scope = conn.execute("select * from campaign_scope_version where id=%s", (camp["current_scope_version_id"],)).fetchone()
    if not scope["authorized_by"]:
        raise CampaignError("scope_not_confirmed", "confirm the scope preview before starting", 422)
    preview = scope_preview(conn, tenant_id, {"ailment_text": scope["ailment_text"], "codes": [c["code"] for c in scope["codes"]], "limits": scope["limits"],
                                              "supplied_source_urls": scope["supplied_source_urls"], "source_policy": scope["source_policy"],
                                              "scope_confirmed": True, "exclusion": scope["exclusion"], "refinements": scope["refinements"]})
    if not preview["can_start"]:
        raise CampaignError("scope_blocked", "; ".join(preview["blocking_reasons"]), 422)
    n = conn.execute("select coalesce(max(run_number),0) as n from campaign_run where campaign_id=%s", (camp["id"],)).fetchone()["n"]
    run = conn.execute("insert into campaign_run(campaign_id, scope_version_id, run_number, state, started_at, heartbeat_at) values (%s,%s,%s,'running',now(),now()) returning *",
                       (camp["id"], scope["id"], n + 1)).fetchone()
    conn.execute("update ingestion_campaign set control='active' where id=%s", (camp["id"],))
    dispatched = _dispatch_sources(conn, tenant_id, camp, scope, run)
    conn.execute("update campaign_run set discovery_closed_at=now(), stop_reason=%s where id=%s",
                 ("discovery closed: bounded source list dispatched" if dispatched["dispatched"] else "no eligible sources within scope and limits", run["id"]))
    _event(conn, camp["id"], run["id"], "started", user_id, dispatched)
    return get(conn, tenant_id, camp["id"])


def _dispatch_sources(conn: psycopg.Connection, tenant_id: Any, camp: dict, scope: dict, run: dict) -> dict[str, Any]:
    """Bounded, request-driven discovery: supplied URLs + allowlisted sources already linked to the scoped conditions.
    Unknown domains become pending items (Needs Attention), never fetches."""
    limits = scope["limits"]
    candidates: list[dict[str, Any]] = []
    for url in scope["supplied_source_urls"]:
        try:
            cu = canonicalize(url)
        except Exception as e:  # noqa: BLE001
            candidates.append({"url": url, "problem": str(e)})
            continue
        src = conn.execute("select * from source where canonical_url=%s", (cu,)).fetchone()
        if not src:
            src = conn.execute("insert into source(tenant_id, canonical_url, source_type, allowlist_state) values (%s,%s,%s,'pending') returning *",
                               (tenant_id, cu, "pdf" if cu.lower().endswith(".pdf") else "html")).fetchone()
        candidates.append({"url": cu, "source": src})
    if scope["source_policy"] != "supplied_only":
        for cid in scope["condition_ids"]:
            rows = conn.execute(
                """select distinct s.* from source s join source_version sv on sv.source_id=s.id join evidence_claim ec on ec.source_version_id=sv.id
                     join clinical_use_version cu on ec.id = any(cu.supporting_claim_ids) where cu.condition_id=%s and s.allowlist_state='approved'""", (cid,)).fetchall()
            candidates += [{"url": r["canonical_url"], "source": r} for r in rows]
    seen: set[str] = set()
    dispatched, pending, skipped = [], [], []
    for c in candidates:
        if c["url"] in seen:
            continue
        seen.add(c["url"])
        if "problem" in c:
            skipped.append({"url": c["url"], "reason": c["problem"]}); continue
        src = c["source"]
        if src["allowlist_state"] != "approved":
            pending.append(c["url"])
            _item(conn, camp["id"], run["id"], "source", src["id"], "pending", {"reason": "domain/source pending allowlist approval"})
            continue
        if len(dispatched) >= int(limits["max_sources"]):
            skipped.append({"url": c["url"], "reason": "max_sources reached"}); continue
        if not conn.execute("select reserve_budget(%s,null,%s::numeric,1,0::bigint) as ok", (run["id"], EST_COST_PER_SOURCE_USD)).fetchone()["ok"]:
            skipped.append({"url": c["url"], "reason": "budget/search-request limit reached"}); continue
        svid = conn.execute("insert into source_version(source_id, final_url, pipeline_state) values (%s,%s,'discovered') returning id", (src["id"], c["url"])).fetchone()["id"]
        grant = conn.execute("select * from rights_grant where source_version_id in (select id from source_version where source_id=%s) order by created_at desc limit 1", (src["id"],)).fetchone()
        if grant:   # carry the source's latest rights grant forward to the new version
            cols = [k for k in grant if k.startswith("can_") or k in ("attribution_required", "attribution_text", "territory", "expires_at", "permission_evidence", "rights_reviewer_id")]
            conn.execute(f"insert into rights_grant(source_version_id, {','.join(cols)}) values (%s,{','.join(['%s'] * len(cols))})",
                         (svid, *[J(grant[k]) if k == "permission_evidence" else grant[k] for k in cols]))
        job = q.enqueue(conn, stage="access_check", source_version_id=svid, payload={"url": c["url"], "region": "unknown"}, tenant_id=tenant_id,
                        campaign_id=camp["id"], campaign_run_id=run["id"], priority=camp["priority"])
        _item(conn, camp["id"], run["id"], "source_version", svid, "new", {"url": c["url"], "job_id": str(job["id"])})
        dispatched.append(c["url"])
    return {"dispatched": dispatched, "pending_allowlist": pending, "skipped": skipped}


def _item(conn: psycopg.Connection, campaign_id: Any, run_id: Any, table: str, item_id: Any, disposition: str, detail: dict) -> None:
    conn.execute("""insert into campaign_item(campaign_id, run_id, item_table, item_id, disposition, detail) values (%s,%s,%s,%s,%s,%s)
                    on conflict (campaign_id, item_table, item_id) do update set disposition=excluded.disposition, detail=excluded.detail""",
                 (campaign_id, run_id, table, item_id, disposition, J(detail)))


def _run_action(conn: psycopg.Connection, tenant_id: Any, campaign_id: Any, expected_revision: int) -> tuple[dict, dict]:
    camp = _camp(conn, tenant_id, campaign_id)
    run = _active_run(conn, camp["id"])
    if not run:
        raise CampaignError("no_active_run", "no active run", 409)
    run = conn.execute("select * from campaign_run where id=%s for update", (run["id"],)).fetchone()
    if run["revision"] != expected_revision:
        raise CampaignError("stale_revision", f"expected revision {expected_revision}, current {run['revision']}")
    return camp, run


def pause(conn, tenant_id, user_id, campaign_id, expected_revision):
    camp, run = _run_action(conn, tenant_id, campaign_id, expected_revision)
    if run["state"] != "running":
        raise CampaignError("not_running", f"run is {run['state']}")
    conn.execute("update campaign_run set state='paused', paused_at=now(), revision=revision+1 where id=%s", (run["id"],))
    conn.execute("update ingestion_campaign set control='paused' where id=%s", (camp["id"],))
    inflight = conn.execute("select count(*) as n from ingestion_job where campaign_run_id=%s and state='running'", (run["id"],)).fetchone()["n"]
    _event(conn, camp["id"], run["id"], "paused", user_id, {"in_flight_jobs_finishing": inflight})
    return get(conn, tenant_id, camp["id"])


def resume(conn, tenant_id, user_id, campaign_id, expected_revision):
    camp, run = _run_action(conn, tenant_id, campaign_id, expected_revision)
    if run["state"] != "paused":
        raise CampaignError("not_paused", f"run is {run['state']}")
    conn.execute("update campaign_run set state='running', paused_at=null, heartbeat_at=now(), revision=revision+1 where id=%s", (run["id"],))
    conn.execute("update ingestion_campaign set control='active' where id=%s", (camp["id"],))
    _event(conn, camp["id"], run["id"], "resumed", user_id, {})
    return get(conn, tenant_id, camp["id"])


def cancel(conn, tenant_id, user_id, campaign_id, expected_revision, reason: str | None = None):
    camp, run = _run_action(conn, tenant_id, campaign_id, expected_revision)
    conn.execute("update campaign_run set state='cancelled', finished_at=now(), stop_reason=%s, revision=revision+1 where id=%s", (f"cancelled by administrator: {reason or 'no reason given'}", run["id"]))
    n = conn.execute("update ingestion_job set state='cancelled', finished_at=now() where campaign_run_id=%s and state='queued' returning id", (run["id"],)).fetchall()
    conn.execute("update ingestion_campaign set control='cancelled', closure_reason=%s where id=%s", (reason, camp["id"]))
    _event(conn, camp["id"], run["id"], "cancelled", user_id, {"queued_jobs_cancelled": len(n), "results_retained": True})
    return get(conn, tenant_id, camp["id"])


def retry_failed(conn, tenant_id, user_id, campaign_id, expected_revision):
    camp, run = _run_action(conn, tenant_id, campaign_id, expected_revision)
    rows = conn.execute("select id, error_class from ingestion_job where campaign_run_id=%s and state in ('dead_letter','failed')", (run["id"],)).fetchall()
    retried = [r["id"] for r in rows if r["error_class"] not in q.PERMANENT_ERRORS and q.retry_dead_letter(conn, r["id"])]
    conn.execute("update campaign_run set revision=revision+1, state='running' where id=%s", (run["id"],))
    _event(conn, camp["id"], run["id"], "retry_failed", user_id, {"retried": len(retried), "not_retryable": len(rows) - len(retried)})
    return get(conn, tenant_id, camp["id"])


# ------------------------------------------------------------ reconciliation, lifecycle, cards
def reconcile_run(conn: psycopg.Connection, run_id: Any) -> dict:
    run = conn.execute("select * from campaign_run where id=%s", (run_id,)).fetchone()
    jobs = {r["state"]: r["n"] for r in conn.execute("select state, count(*) as n from ingestion_job where campaign_run_id=%s group by state", (run_id,)).fetchall()}
    active = jobs.get("queued", 0) + jobs.get("running", 0)
    last_hb = conn.execute("select max(heartbeat_at) as hb from ingestion_job where campaign_run_id=%s", (run_id,)).fetchone()["hb"]
    if last_hb:
        conn.execute("update campaign_run set heartbeat_at=greatest(coalesce(heartbeat_at, %s), %s) where id=%s", (last_hb, last_hb, run_id))
    if run["state"] == "running" and active == 0 and run["discovery_closed_at"]:
        conn.execute("update campaign_run set state='finished', finished_at=now(), stop_reason=coalesce(stop_reason,'') || '; queue drained' where id=%s", (run_id,))
    spent = conn.execute("select coalesce(sum(cost_usd),0) as c from ingestion_job where campaign_run_id=%s", (run_id,)).fetchone()["c"]
    progress = {"jobs": jobs, "spent_usd_actual": float(spent)}
    conn.execute("update campaign_run set progress=%s where id=%s", (J(progress), run_id))
    return conn.execute("select * from campaign_run where id=%s", (run_id,)).fetchone()


def counts(conn: psycopg.Connection, campaign_id: Any) -> dict[str, int]:
    c: dict[str, int] = {}
    for r in conn.execute("select disposition, count(*) as n from campaign_item where campaign_id=%s and item_table in ('source_version','source') group by disposition", (campaign_id,)).fetchall():
        c[f"sources_{r['disposition']}"] = r["n"]
    c["sources_discovered"] = sum(v for k, v in c.items() if k.startswith("sources_"))
    c["sources_processed"] = conn.execute("select count(distinct source_version_id) as n from ingestion_job where campaign_id=%s and stage='enqueue_review' and state='succeeded'", (campaign_id,)).fetchone()["n"]
    for r in conn.execute("select state, count(*) as n from ingestion_job where campaign_id=%s group by state", (campaign_id,)).fetchall():
        c[f"jobs_{r['state']}"] = r["n"]
    v = conn.execute(
        """select count(*) filter (where v.duplicate_of_entity_id is null) as new_variants, count(*) filter (where v.duplicate_of_entity_id is not null) as duplicates,
                  count(*) filter (where v.approval_state='pending_review') as awaiting_review, count(*) filter (where v.approval_state='approved') as approved,
                  count(*) filter (where v.approval_state='published') as published, count(*) filter (where v.approval_state='rejected') as rejected
             from exercise_variant_version v join dependency_edge d on d.downstream_id=v.id and d.upstream_table='source_version'
             join campaign_item ci on ci.item_id = d.upstream_id and ci.item_table='source_version' where ci.campaign_id=%s""", (campaign_id,)).fetchone()
    c.update({k: int(v[k] or 0) for k in v})
    c["evidence_linked"] = conn.execute("select count(*) as n from evidence_claim ec join campaign_item ci on ci.item_id=ec.source_version_id and ci.item_table='source_version' where ci.campaign_id=%s", (campaign_id,)).fetchone()["n"]
    c["reused_variants"] = conn.execute("select count(*) as n from campaign_item where campaign_id=%s and item_table='exercise_variant_version' and disposition='reused'", (campaign_id,)).fetchone()["n"]
    return c


def derive_lifecycle(conn: psycopg.Connection, camp: dict, run: dict | None, cnt: dict[str, int]) -> tuple[str, list[str], str | None]:
    blockers: list[str] = []
    if camp["control"] == "cancelled":
        return ("closed_incomplete" if camp["lifecycle"] != "complete" else "complete"), ["cancelled"], "propose a new run if the request is still needed"
    if not run:
        return "draft", [], "confirm scope and start"
    if run["state"] == "pending":
        return "queued", [], None
    if run["state"] in ("running", "paused"):
        if cnt.get("jobs_dead_letter", 0):
            blockers.append(f"{cnt['jobs_dead_letter']} job(s) dead-lettered; retry or decide")
        if cnt.get("sources_pending", 0):
            blockers.append(f"{cnt['sources_pending']} source(s) pending allowlist approval")
        active = cnt.get("jobs_queued", 0) + cnt.get("jobs_running", 0)
        if active == 0 and blockers:
            return "needs_attention", blockers, blockers[0]
        return "running", blockers, None
    # finished / cancelled run
    if cnt.get("jobs_dead_letter", 0):
        blockers.append(f"{cnt['jobs_dead_letter']} job(s) dead-lettered")
    if cnt.get("jobs_failed", 0):
        blockers.append(f"{cnt['jobs_failed']} job(s) failed permanently (rights/allowlist/schema)")
    if cnt.get("sources_pending", 0):
        blockers.append(f"{cnt['sources_pending']} source(s) pending allowlist approval")
    checks = conn.execute("select state, count(*) as n from campaign_coverage_check where campaign_id=%s group by state", (camp["id"],)).fetchall()
    ck = {r["state"]: r["n"] for r in checks}
    if ck and ck.get("met", 0) == sum(ck.values()) and cnt.get("published", 0) > 0 and cnt.get("awaiting_review", 0) == 0:
        return "complete", [], None
    if cnt.get("awaiting_review", 0) or cnt.get("approved", 0) or cnt.get("published", 0):
        return "pt_review", blockers, "PT reviews candidates; clinical lead publishes"
    blockers.append("run produced no reviewable candidates within scope and limits")
    return "needs_attention", blockers, "widen sources, raise limits, or close incomplete"


def card(conn: psycopg.Connection, camp: dict) -> dict[str, Any]:
    run = _active_run(conn, camp["id"]) or conn.execute("select * from campaign_run where campaign_id=%s order by run_number desc limit 1", (camp["id"],)).fetchone()
    if run:
        run = reconcile_run(conn, run["id"])
    scope = conn.execute("select * from campaign_scope_version where id=%s", (camp["current_scope_version_id"],)).fetchone()
    cnt = counts(conn, camp["id"])
    lifecycle, blockers, next_action = derive_lifecycle(conn, camp, run, cnt)
    if lifecycle != camp["lifecycle"]:
        conn.execute("update ingestion_campaign set lifecycle=%s where id=%s", (lifecycle, camp["id"]))
    owner = conn.execute("select display_name from app_user where id=%s", (camp["owner_id"],)).fetchone() if camp["owner_id"] else None
    reviewer = conn.execute("select display_name from app_user where id=%s", (camp["reviewer_id"],)).fetchone() if camp["reviewer_id"] else None
    now = datetime.now(timezone.utc)
    hb = run["heartbeat_at"] if run else None
    stale = bool(run and run["state"] == "running" and (hb is None or now - hb > timedelta(seconds=HEARTBEAT_STALE_S)) and (cnt.get("jobs_queued", 0) + cnt.get("jobs_running", 0)) > 0)
    budget = run["budget"] if run else {"reserved_usd": 0, "spent_usd": 0}
    spend = float(budget.get("spent_usd", 0)) + float((run["progress"] or {}).get("spent_usd_actual", 0) if run else 0)
    conds = conn.execute("select preferred_name from condition where id = any(%s)", (scope["condition_ids"],)).fetchall() if scope else []
    allowed = allowed_actions(camp, run, scope)
    cov = {r["state"]: r["n"] for r in conn.execute("select state, count(*) as n from campaign_coverage_check where campaign_id=%s group by state", (camp["id"],)).fetchall()}
    activity = None
    if run and run["state"] == "running":
        activity = f"{cnt.get('jobs_running', 0)} running, {cnt.get('jobs_queued', 0)} queued"
    elif run:
        activity = f"run {run['run_number']} {run['state']}: {run['stop_reason'] or ''}".strip()
    return {"id": str(camp["id"]), "title": camp["title"], "lifecycle": lifecycle, "control": camp["control"], "priority": camp["priority"],
            "owner": owner["display_name"] if owner else None, "reviewer": reviewer["display_name"] if reviewer else None,
            "condition_summary": ", ".join(c["preferred_name"] for c in conds) or (scope["ailment_text"] if scope else None),
            "scope_summary": (f"{scope['desired_output']}; {scope['source_policy']}; media {scope['media_policy']}" if scope else None),
            "current_activity": activity, "last_update": camp["updated_at"], "heartbeat_at": hb, "heartbeat_stale": stale,
            "counts": cnt, "spend_usd": round(spend, 4), "cap_usd": float(scope["limits"]["max_usd"]) if scope else 0.0,
            "active_seconds": int(((run["finished_at"] or now) - run["started_at"]).total_seconds()) if run and run["started_at"] else 0,
            "coverage": cov, "blockers": blockers, "next_human_action": next_action, "allowed_actions": allowed,
            "run_id": str(run["id"]) if run else None, "run_revision": run["revision"] if run else None}


def allowed_actions(camp: dict, run: dict | None, scope: dict | None) -> list[str]:
    a = ["edit", "preview_scope", "assign_reviewer", "change_priority", "propose_new_run"]
    if camp["control"] == "cancelled":
        return a
    if not run or run["state"] in ("finished", "cancelled"):
        a.append("start")
    elif run["state"] == "running":
        a += ["pause", "cancel", "retry_failed"]
    elif run["state"] == "paused":
        a += ["resume", "cancel", "retry_failed"]
    elif run["state"] == "pending":
        a += ["cancel"]
    return a


def get(conn: psycopg.Connection, tenant_id: Any, campaign_id: Any) -> dict[str, Any]:
    camp = _camp(conn, tenant_id, campaign_id)
    c = card(conn, camp)
    scope = conn.execute("select * from campaign_scope_version where id=%s", (camp["current_scope_version_id"],)).fetchone()
    runs = conn.execute("select id, run_number, state, revision, started_at, finished_at, stop_reason, budget, progress, heartbeat_at from campaign_run where campaign_id=%s order by run_number", (camp["id"],)).fetchall()
    checks = conn.execute("select * from campaign_coverage_check where campaign_id=%s order by criterion", (camp["id"],)).fetchall()
    warnings = []
    if scope and not scope["authorized_by"]:
        warnings.append("scope not confirmed")
    return {**c, "scope": {k: (str(v) if isinstance(v, uuid.UUID) else v) for k, v in scope.items()} if scope else {},
            "runs": [{**r, "id": str(r["id"])} for r in runs], "coverage_checks": [{**k, "id": str(k["id"])} for k in checks],
            "limits": scope["limits"] if scope else {}, "budget": runs[-1]["budget"] if runs else {}, "warnings": warnings,
            "maintenance_enabled": camp["maintenance_enabled"]}


def list_cards(conn: psycopg.Connection, tenant_id: Any, *, lifecycle: str | None = None, control: str | None = None, include_archived: bool = False) -> list[dict[str, Any]]:
    rows = conn.execute("select * from ingestion_campaign where tenant_id=%s order by priority, created_at", (tenant_id,)).fetchall()
    out = []
    for r in rows:
        c = card(conn, r)
        if not include_archived and c["control"] == "cancelled" and not lifecycle:
            continue
        if lifecycle and c["lifecycle"] != lifecycle:
            continue
        if control and c["control"] != control:
            continue
        out.append(c)
    return out


def set_coverage(conn: psycopg.Connection, tenant_id: Any, user_id: Any, campaign_id: Any, criterion: str, state: str, evidence: dict | None) -> dict:
    camp = _camp(conn, tenant_id, campaign_id)
    run = _active_run(conn, camp["id"])
    return conn.execute(
        """insert into campaign_coverage_check(campaign_id, run_id, criterion, state, evidence, reviewer_id) values (%s,%s,%s,%s,%s,%s)
           on conflict (campaign_id, criterion) do update set state=excluded.state, evidence=excluded.evidence, reviewer_id=excluded.reviewer_id, updated_at=now() returning *""",
        (camp["id"], run["id"] if run else None, criterion, state, J(evidence or {}), user_id)).fetchone()


def events_since(conn: psycopg.Connection, tenant_id: Any, after_id: int, limit: int = 200) -> list[dict[str, Any]]:
    return conn.execute(
        """select e.* from campaign_event e join ingestion_campaign c on c.id=e.campaign_id where c.tenant_id=%s and e.id > %s order by e.id limit %s""",
        (tenant_id, after_id, limit)).fetchall()
