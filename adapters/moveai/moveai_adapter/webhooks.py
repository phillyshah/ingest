"""Outbox dispatcher: signed webhooks with replay protection, bounded retries, dead-letter (spec §10)."""

from __future__ import annotations

import hashlib
import hmac
import json
import os
from typing import Any

import httpx
import psycopg

MAX_ATTEMPTS = 8


def sign(secret: str, body: bytes) -> str:
    return hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()


def render(event: dict[str, Any]) -> bytes:
    payload = {
        "id": str(event["id"]),
        "type": event["event_type"],
        "schema_version": event["schema_version"],
        "tenant_id": str(event["tenant_id"]) if event["tenant_id"] else None,
        "object": {
            "table": event["object_table"],
            "id": str(event["object_id"]) if event["object_id"] else None,
            "version": event["object_version"],
        },
        "occurred_at": event["created_at"].isoformat(),
        "data": event["payload"],
    }
    return json.dumps(payload, sort_keys=True).encode()


def dispatch_pending(
    conn: psycopg.Connection,
    *,
    url: str | None = None,
    secret: str | None = None,
    transport: Any = None,
    limit: int = 100,
) -> dict[str, int]:
    url = url or os.environ.get("MOVEAI_WEBHOOK_URL")
    secret = secret or os.environ.get("MOVEAI_WEBHOOK_SECRET", "")
    rows = conn.execute(
        "select * from outbox_event where delivered_at is null and not dead_lettered order by created_at limit %s",
        (limit,),
    ).fetchall()
    stats = {"delivered": 0, "failed": 0, "dead_lettered": 0, "skipped": 0}
    if not url:
        stats["skipped"] = len(rows)
        return stats
    with httpx.Client(transport=transport, timeout=10) as client:
        for ev in rows:
            body = render(ev)
            try:
                r = client.post(
                    url,
                    content=body,
                    headers={
                        "content-type": "application/json",
                        "x-moveai-signature": sign(secret, body),
                        "x-moveai-event-id": str(ev["id"]),
                    },
                )
                ok = r.status_code < 300
            except httpx.HTTPError:
                ok = False
            if ok:
                conn.execute("update outbox_event set delivered_at=now(), attempts=attempts+1 where id=%s", (ev["id"],))
                stats["delivered"] += 1
            else:
                dead = ev["attempts"] + 1 >= MAX_ATTEMPTS
                conn.execute(
                    "update outbox_event set attempts=attempts+1, dead_lettered=%s where id=%s",
                    (dead, ev["id"]),
                )
                stats["dead_lettered" if dead else "failed"] += 1
    return stats
