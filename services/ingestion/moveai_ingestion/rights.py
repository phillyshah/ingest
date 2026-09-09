"""Rights gate (spec §4). Every operation is allowed|denied|unknown; unknown blocks."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

import psycopg
from moveai_contracts.api import PERMISSION_OPS


@dataclass(frozen=True)
class RightsCheck:
    allowed: bool
    reason: str | None = None
    error_class: str | None = None


def check(grant: dict[str, Any] | None, op: str, at: datetime | None = None) -> RightsCheck:
    if op not in PERMISSION_OPS:
        raise ValueError(f"unknown permission op {op}")
    if grant is None:
        return RightsCheck(False, f"no rights grant recorded; {op} is unknown", "rights_unknown")
    at = at or datetime.now(UTC)
    if grant.get("revoked_at") and grant["revoked_at"] <= at:
        return RightsCheck(False, "rights grant revoked", "rights_denied")
    if grant.get("expires_at") and grant["expires_at"] <= at:
        return RightsCheck(False, "rights grant expired", "rights_denied")
    state = grant.get(op, "unknown")
    if state == "allowed":
        return RightsCheck(True)
    if state == "denied":
        return RightsCheck(False, f"{op} denied by rights grant", "rights_denied")
    return RightsCheck(False, f"{op} is unknown; unknown blocks the use", "rights_unknown")


def grant_for_source_version(conn: psycopg.Connection, source_version_id: Any) -> dict | None:
    return conn.execute(
        "select * from rights_grant where source_version_id=%s order by created_at desc limit 1",
        (source_version_id,),
    ).fetchone()


def grant_for_media(conn: psycopg.Connection, media_version_id: Any) -> dict | None:
    return conn.execute(
        "select * from rights_grant where media_asset_version_id=%s order by created_at desc limit 1",
        (media_version_id,),
    ).fetchone()


def patient_display_allowed(grant: dict | None) -> bool:
    return check(grant, "can_display_to_patient").allowed
