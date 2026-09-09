"""Immutable catalog releases (spec §5.10, §10). Only approved versions enter; unsigned placeholders never do."""

from __future__ import annotations

import hashlib
import json
from typing import Any

import psycopg
from moveai_db import J

RELEASABLE_TABLES = (
    "exercise_variant_version",
    "clinical_use_version",
    "protocol_version",
    "rule_version",
    "media_asset_version",
    "population_applicability_version",
    "diagnosis_mapping_version",
    "segmentation_definition_version",
)


def row_hash(row: dict[str, Any]) -> str:
    frozen = {
        k: v
        for k, v in row.items()
        if k
        not in (
            "approval_state",
            "updated_at",
            "approved_at",
            "invalidated_at",
            "withdrawn_at",
            "withdrawal_reason",
            "superseded_by_id",
        )
    }
    return hashlib.sha256(json.dumps(frozen, sort_keys=True, default=str).encode()).hexdigest()


def publish(conn: psycopg.Connection, *, label: str, published_by: Any, tables: list[str] | None = None) -> dict[str, Any]:
    tables = [t for t in (tables or RELEASABLE_TABLES) if t in RELEASABLE_TABLES]
    manifest = []
    for t in tables:
        rows = (
            conn.execute(f"select * from {t} where approval_state in ('approved','published') and (tenant_id is null or true)").fetchall()
            if t
            not in (
                "segmentation_definition_version",
                "diagnosis_mapping_version",
                "population_applicability_version",
                "media_asset_version",
            )
            else conn.execute(f"select * from {t} where approval_state in ('approved','published')").fetchall()
        )
        for r in rows:
            manifest.append({"table": t, "version_id": str(r["id"]), "content_hash": row_hash(r)})
    prior = conn.execute("select id from catalog_release order by published_at desc limit 1").fetchone()
    msha = hashlib.sha256(json.dumps(manifest, sort_keys=True).encode()).hexdigest()
    rel = conn.execute(
        "insert into catalog_release(label, manifest, manifest_sha256, published_by, prior_release_id) values (%s,%s,%s,%s,%s) returning *",
        (label, J(manifest), msha, published_by, prior["id"] if prior else None),
    ).fetchone()
    for m in manifest:
        conn.execute(
            "insert into catalog_release_item(release_id, table_name, version_id, content_hash) values (%s,%s,%s,%s)",
            (rel["id"], m["table"], m["version_id"], m["content_hash"]),
        )
        conn.execute(
            f"update {m['table']} set approval_state='published' where id=%s and approval_state='approved'",
            (m["version_id"],),
        )
        conn.execute(
            "insert into catalog_change(change_type, entity_table, version_id, release_id) values ('published',%s,%s,%s)",
            (m["table"], m["version_id"], rel["id"]),
        )
    conn.execute(
        "insert into outbox_event(event_type, object_table, object_id, payload) values ('catalog.published','catalog_release',%s,%s)",
        (rel["id"], J({"label": label, "manifest_sha256": msha, "items": len(manifest)})),
    )
    return {
        "id": str(rel["id"]),
        "label": label,
        "manifest_sha256": msha,
        "item_count": len(manifest),
        "published_at": rel["published_at"],
    }
