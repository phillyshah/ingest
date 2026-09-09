"""Mock MoveAI client (spec §11). MoveAI never writes catalog tables; it reads approved plans and the change feed
through the versioned API with an integration-role credential, deduplicating events by id."""

from __future__ import annotations

import hashlib
import hmac
import json
from dataclasses import dataclass, field
from typing import Any

import httpx


@dataclass
class MoveAIMockClient:
    base_url: str  # e.g. https://ingest.phillyshah.com/api/v1 or http://127.0.0.1:8000/v1
    user_id: str  # integration service account (dev shim) / bearer token in production
    tenant_id: str
    webhook_secret: str | None = None
    seen_event_ids: set[str] = field(default_factory=set)
    change_cursor: int = 0
    transport: Any = None  # httpx transport override for tests
    http: httpx.Client | None = None  # pre-built client (e.g. a FastAPI TestClient) for in-process tests

    def _client(self) -> httpx.Client:
        headers = {"X-User-Id": self.user_id, "X-Tenant-Id": self.tenant_id, "X-Role": "integration"}
        if self.http is not None:
            self.http.headers.update(headers)
            return self.http
        return httpx.Client(base_url=self.base_url, headers=headers, transport=self.transport, timeout=30)

    def get_approved_plan(self, plan_id: str) -> dict[str, Any]:
        """Retrieve the pinned approved version, or an explicit unavailable status. Never a draft."""
        with self._client() as c:
            r = c.get(f"/plans/{plan_id}/approved")
        if r.status_code in (403, 404):
            return {"available": False, "reason": f"HTTP {r.status_code}"}
        r.raise_for_status()
        body = r.json()
        if body.get("available"):
            assert "items" in body and body["approval_signature"], "approved plan must carry items and a signature"
        return body

    def poll_changes(self, limit: int = 100) -> list[dict[str, Any]]:
        """Reconcile after outages: cursor-based catalog updates and withdrawals."""
        out: list[dict[str, Any]] = []
        with self._client() as c:
            while True:
                r = c.get("/changes", params={"cursor": self.change_cursor, "limit": limit})
                r.raise_for_status()
                body = r.json()
                out.extend(body["items"])
                if body["items"]:
                    self.change_cursor = max(int(i["cursor"]) for i in body["items"])
                if not body.get("next_cursor"):
                    break
        return out

    def handle_webhook(self, headers: dict[str, str], raw_body: bytes) -> dict[str, Any] | None:
        """Verify signature and deduplicate by event id. Returns the event or None if duplicate/invalid."""
        if self.webhook_secret:
            sig = headers.get("x-moveai-signature", "")
            expected = hmac.new(self.webhook_secret.encode(), raw_body, hashlib.sha256).hexdigest()
            if not hmac.compare_digest(sig, expected):
                return None
        event = json.loads(raw_body)
        eid = event.get("id")
        if not eid or eid in self.seen_event_ids:
            return None
        self.seen_event_ids.add(eid)
        return event

    # Catalog writes are deliberately absent: the adapter has no method that mutates catalog tables.
