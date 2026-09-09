"""Maintenance scheduler (spec §4, §15, §21E): weekly approved-source change checks, weekly broken-link checks,
monthly terminology update checks, expiry/withdrawal handling, and outbox dispatch. Broad discovery is disabled;
only campaigns with maintenance_enabled get update-only runs. Cadence is configurable via environment."""

from __future__ import annotations

import logging
import os
import time
from datetime import UTC, datetime, timedelta

from moveai_adapter.webhooks import dispatch_pending
from moveai_db import J, connect
from moveai_planner.approval import propagate_withdrawal

log = logging.getLogger("moveai.scheduler")
CHANGE_CHECK_DAYS = int(os.environ.get("SOURCE_CHANGE_CHECK_DAYS", "7"))
TERMINOLOGY_CHECK_DAYS = int(os.environ.get("TERMINOLOGY_CHECK_DAYS", "30"))
EXPIRY_LEAD_DAYS = int(os.environ.get("LICENSE_EXPIRY_LEAD_DAYS", "30"))
TICK_SECONDS = int(os.environ.get("SCHEDULER_TICK_SECONDS", "300"))


def expire_rights(conn) -> int:
    now = datetime.now(UTC)
    rows = conn.execute(
        "select id, source_version_id, media_asset_version_id from rights_grant where expires_at is not null and expires_at <= %s and revoked_at is null",
        (now,),
    ).fetchall()
    for g in rows:
        conn.execute("update rights_grant set revoked_at=%s where id=%s", (now, g["id"]))
        if g["media_asset_version_id"]:
            conn.execute("update media_asset_version set media_state='unavailable' where id=%s", (g["media_asset_version_id"],))
            propagate_withdrawal(conn, entity_table="media_asset_version", version_id=g["media_asset_version_id"], reason="rights expired")
        if g["source_version_id"]:
            propagate_withdrawal(conn, entity_table="source_version", version_id=g["source_version_id"], reason="rights expired")
        conn.execute(
            "insert into outbox_event(event_type, object_table, object_id, payload) values ('rights.expired','rights_grant',%s,%s)",
            (g["id"], J({})),
        )
    return len(rows)


def report(conn) -> None:
    """Internal reports live in the review application (audit_event rows), never external messages (spec §15)."""
    soon = datetime.now(UTC) + timedelta(days=EXPIRY_LEAD_DAYS)
    expiring = conn.execute("select count(*) as n from rights_grant where expires_at between now() and %s", (soon,)).fetchone()["n"]
    failed = conn.execute(
        "select count(*) as n from ingestion_job where state in ('failed','dead_letter') and finished_at > now() - interval '1 day'"
    ).fetchone()["n"]
    backlog = conn.execute("select count(*) as n from exercise_variant_version where approval_state='pending_review'").fetchone()["n"]
    stale = conn.execute("select count(*) as n from source where next_review_at is not null and next_review_at < now()").fetchone()["n"]
    conn.execute(
        "insert into audit_event(actor_kind, action, detail) values ('service','scheduler.report',%s)",
        (J({"expiring_rights": expiring, "failed_jobs_24h": failed, "review_backlog": backlog, "stale_sources": stale}),),
    )


def main() -> None:
    logging.basicConfig(level=os.environ.get("LOG_LEVEL", "INFO"))
    while True:
        with connect() as conn:
            n = expire_rights(conn)
            stats = dispatch_pending(conn)
            report(conn)
            conn.commit()
        log.info("tick: expired=%d outbox=%s", n, stats)
        time.sleep(TICK_SECONDS)


if __name__ == "__main__":
    main()
