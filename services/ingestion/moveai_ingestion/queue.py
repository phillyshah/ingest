"""Postgres-backed at-least-once job queue (ADR-0002, spec §5 reliability)."""

from __future__ import annotations

import hashlib
import random
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import psycopg
from moveai_db import J

from .config import PARSER_VERSION, PROMPT_VERSION, SCHEMA_VERSION, model_version

STAGES = ("access_check", "fetch", "parse", "extract", "normalize", "validate", "enqueue_review")
PERMANENT_ERRORS = {
    "rights_denied",
    "rights_unknown",
    "not_allowlisted",
    "schema_invalid",
    "quarantined",
    "ssrf_blocked",
}
LEASE_SECONDS = 120


def idempotency_key(source_version_id: uuid.UUID | str | None, stage: str, extra: str = "") -> str:
    raw = f"{source_version_id}|{stage}|{PARSER_VERSION}|{SCHEMA_VERSION}|{PROMPT_VERSION}|{model_version()}|{extra}"
    return hashlib.sha256(raw.encode()).hexdigest()


def enqueue(
    conn: psycopg.Connection,
    *,
    stage: str,
    source_version_id: Any = None,
    payload: dict | None = None,
    tenant_id: Any = None,
    campaign_id: Any = None,
    campaign_run_id: Any = None,
    priority: int = 100,
    extra_key: str = "",
) -> dict:
    """Idempotent: re-enqueueing the same (source_version, stage, versions) returns the existing job."""
    key = idempotency_key(source_version_id, stage, extra_key)
    row = conn.execute(
        """insert into ingestion_job(tenant_id, source_version_id, stage, idempotency_key, payload, campaign_id, campaign_run_id, priority)
           values (%s,%s,%s,%s,%s,%s,%s,%s)
           on conflict (idempotency_key) do update set idempotency_key = excluded.idempotency_key
           returning id, state, (xmax = 0) as inserted""",
        (tenant_id, source_version_id, stage, key, J(payload or {}), campaign_id, campaign_run_id, priority),
    ).fetchone()
    if row["inserted"]:
        conn.execute(
            "insert into job_event(job_id, event, detail) values (%s,'enqueued',%s)",
            (row["id"], J({"stage": stage})),
        )
    return row


def claim(conn: psycopg.Connection, worker_id: str, stages: tuple[str, ...] = STAGES) -> dict | None:
    row = conn.execute(
        """with c as (
             select id from ingestion_job
              where state = 'queued' and run_after <= now() and stage = any(%s)
                and (campaign_run_id is null or campaign_run_id in (select id from campaign_run where state = 'running'))
              order by priority, run_after
              for update skip locked limit 1)
           update ingestion_job j set state='running', attempts = attempts + 1, worker_id = %s,
                  leased_until = now() + make_interval(secs => %s), heartbeat_at = now()
             from c where j.id = c.id
           returning j.*""",
        (list(stages), worker_id, LEASE_SECONDS),
    ).fetchone()
    if row:
        conn.execute(
            "insert into job_event(job_id, event, detail) values (%s,'claimed',%s)",
            (row["id"], J({"worker": worker_id, "attempt": row["attempts"]})),
        )
    return row


def heartbeat(conn: psycopg.Connection, job_id: Any) -> None:
    conn.execute(
        "update ingestion_job set heartbeat_at = now(), leased_until = now() + make_interval(secs => %s) where id=%s",
        (LEASE_SECONDS, job_id),
    )


def checkpoint(conn: psycopg.Connection, job_id: Any, data: dict) -> None:
    conn.execute("update ingestion_job set checkpoint = checkpoint || %s where id=%s", (J(data), job_id))


def succeed(
    conn: psycopg.Connection,
    job_id: Any,
    *,
    warnings: list | None = None,
    cost_usd: float = 0,
    duration_ms: int = 0,
) -> None:
    conn.execute(
        """update ingestion_job set state='succeeded', finished_at=now(), warnings=%s, cost_usd = cost_usd + %s,
                    duration_ms=%s, leased_until=null where id=%s""",
        (J(warnings or []), cost_usd, duration_ms, job_id),
    )
    conn.execute("insert into job_event(job_id, event) values (%s,'succeeded')", (job_id,))


def fail(conn: psycopg.Connection, job_id: Any, error_class: str, message: str, *, permanent: bool | None = None) -> str:
    """Returns the resulting state. Retries use bounded exponential backoff with jitter."""
    job = conn.execute("select attempts, max_attempts from ingestion_job where id=%s", (job_id,)).fetchone()
    permanent = permanent if permanent is not None else error_class in PERMANENT_ERRORS
    if permanent or job["attempts"] >= job["max_attempts"]:
        state = "dead_letter" if not permanent else "failed"
        conn.execute(
            "update ingestion_job set state=%s, error_class=%s, last_error=%s, finished_at=now(), leased_until=null where id=%s",
            (state, error_class, message[:2000], job_id),
        )
    else:
        delay = min(300, (2 ** job["attempts"]) + random.uniform(0, 1.0))
        state = "queued"
        conn.execute(
            "update ingestion_job set state='queued', error_class=%s, last_error=%s, run_after = now() + make_interval(secs => %s), leased_until=null where id=%s",
            (error_class, message[:2000], delay, job_id),
        )
    conn.execute(
        "insert into job_event(job_id, event, detail) values (%s,'failed',%s)",
        (job_id, J({"error_class": error_class, "message": message[:500], "state": state})),
    )
    return state


def reclaim_expired(conn: psycopg.Connection) -> int:
    """Lost-worker recovery: expired leases go back to queued (liveness timeout, spec §21E)."""
    rows = conn.execute(
        """update ingestion_job set state='queued', leased_until=null, worker_id=null
            where state='running' and leased_until < now() returning id"""
    ).fetchall()
    for r in rows:
        conn.execute(
            "insert into job_event(job_id, event, detail) values (%s,'reclaimed',%s)",
            (r["id"], J({"reason": "lease expired"})),
        )
    return len(rows)


def retry_dead_letter(conn: psycopg.Connection, job_id: Any) -> bool:
    row = conn.execute(
        "update ingestion_job set state='queued', attempts=0, run_after=now(), error_class=null where id=%s and state in ('dead_letter','failed') returning id",
        (job_id,),
    ).fetchone()
    if row:
        conn.execute("insert into job_event(job_id, event) values (%s,'manual_retry')", (job_id,))
    return bool(row)


def utcnow() -> datetime:
    return datetime.now(UTC)


def _unused(td: timedelta) -> None:  # keep timedelta import meaningful for type checkers
    return None
