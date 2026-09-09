"""ICD-10-CM release import (spec §6 diagnostic coding). Reads official order-file layout; effective dates come
from release metadata, never hardcoded. Resolves codes for a service date."""

from __future__ import annotations

import hashlib
from datetime import date
from pathlib import Path
from typing import Any

import psycopg
import yaml

LATERALITY_WORDS = {"right": "right", "left": "left", "bilateral": "bilateral", "unspecified": "unspecified"}


def _laterality(desc: str) -> str:
    d = desc.lower()
    for w, v in LATERALITY_WORDS.items():
        if f" {w} " in f" {d} " or d.endswith(f" {w}") or f", {w}" in d:
            return v
    return "not_applicable"


def parse_order_file(path: Path) -> list[dict[str, Any]]:
    rows = []
    for line in path.read_text().splitlines():
        if len(line) < 16:
            continue
        code_raw = line[6:13].strip()
        flag = line[14:15]
        long_desc = line[77:].strip() if len(line) > 77 else line[16:].strip()
        code = code_raw if len(code_raw) <= 3 else f"{code_raw[:3]}.{code_raw[3:]}"
        rows.append(
            {
                "code": code,
                "descriptor": long_desc,
                "billable": flag == "1",
                "laterality": _laterality(long_desc),
            }
        )
    return rows


def import_releases(conn: psycopg.Connection, fixtures_dir: Path) -> list[dict[str, Any]]:
    meta = yaml.safe_load((fixtures_dir / "releases.yaml").read_text())
    out = []
    for rel in meta:
        f = fixtures_dir / rel["file"]
        h = hashlib.sha256(f.read_bytes()).hexdigest()
        row = conn.execute(
            """insert into terminology_release(system, release_id, effective_start, effective_end, source_hash, source_url)
               values (%s,%s,%s,%s,%s,%s) on conflict (system, release_id) do update set source_hash = excluded.source_hash
               returning id""",
            (
                rel["system"],
                rel["release_id"],
                rel["effective_start"],
                rel["effective_end"],
                h,
                rel["source_url"],
            ),
        ).fetchone()
        for c in parse_order_file(f):
            conn.execute(
                """insert into terminology_code(release_id, code, descriptor, billable, laterality) values (%s,%s,%s,%s,%s)
                   on conflict (release_id, code) do update set descriptor = excluded.descriptor, billable = excluded.billable""",
                (row["id"], c["code"], c["descriptor"], c["billable"], c["laterality"]),
            )
        out.append({"release_id": rel["release_id"], "id": row["id"], "codes": len(parse_order_file(f))})
    return out


def release_for_date(conn: psycopg.Connection, system: str, service_date: date) -> dict | None:
    return conn.execute(
        """select * from terminology_release where system=%s and effective_start <= %s and (effective_end is null or effective_end >= %s)
           order by effective_start desc limit 1""",
        (system, service_date, service_date),
    ).fetchone()


def resolve_code(conn: psycopg.Connection, code: str, service_date: date, system: str = "ICD-10-CM") -> dict[str, Any]:
    rel = release_for_date(conn, system, service_date)
    if not rel:
        return {
            "code": code,
            "resolved": False,
            "note": f"no {system} release effective on {service_date}",
            "descriptor": None,
            "release_id": None,
            "release_label": None,
            "laterality": None,
            "billable": None,
        }
    row = conn.execute("select * from terminology_code where release_id=%s and code=%s", (rel["id"], code.upper())).fetchone()
    if not row:
        return {
            "code": code,
            "resolved": False,
            "note": f"code not found in {rel['release_id']}",
            "descriptor": None,
            "release_id": str(rel["id"]),
            "release_label": rel["release_id"],
            "laterality": None,
            "billable": None,
        }
    return {
        "code": row["code"],
        "resolved": True,
        "descriptor": row["descriptor"],
        "release_id": str(rel["id"]),
        "release_label": rel["release_id"],
        "laterality": row["laterality"],
        "billable": row["billable"],
        "note": None if row["billable"] else "category code: valid as a search scope, not billable",
    }
