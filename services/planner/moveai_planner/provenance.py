"""Server-side checks that a dose's stated provenance is real (spec §3B, §8).

`DoseField` validates the *shape* of provenance — a `source_explicit` value must name a `claim_id`, a
`clinician_authored` value must name an `author_id` — but shape is not truth. Before this module, any UUID passed as a
claim id and any UUID passed as an author, so a number nobody extracted could be dressed up as source-backed. Every
writer of dose fields (review edits, draft patches, pack install) calls through here.
"""

from __future__ import annotations

from typing import Any

import psycopg


class ProvenanceError(ValueError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code, self.message = code, message


def _upstream_source_version(conn: psycopg.Connection, variant_version_id: Any) -> Any | None:
    edge = conn.execute(
        "select upstream_id from dependency_edge where downstream_table='exercise_variant_version' and downstream_id=%s "
        "and upstream_table='source_version' and dependency_type='derived_from' limit 1",
        (variant_version_id,),
    ).fetchone()
    return edge["upstream_id"] if edge else None


def verify_dose_fields(
    conn: psycopg.Connection,
    fields: dict[str, Any],
    *,
    variant_version_id: Any | None,
    actor_id: Any | None = None,
) -> None:
    """Raise ProvenanceError unless every set field's provenance can be substantiated.

    - `source_explicit`: the claim exists, is a `dose` claim, and — when the variant was extracted from a source —
      comes from that same source version. A dose lifted from a different document than the exercise it is attached
      to is exactly the mismatch a reviewer cannot see from a UUID.
    - `clinician_authored`: when the caller is known, the author named must be the caller. Nobody records a number
      under someone else's name.
    Fields with no value are not provenance claims and are not checked.
    """
    if not fields:
        return
    src_version = _upstream_source_version(conn, variant_version_id) if variant_version_id else None
    for name, f in fields.items():
        if not isinstance(f, dict):
            f = f.model_dump() if hasattr(f, "model_dump") else dict(f)
        if f.get("value") is None and not f.get("range"):
            continue
        prov = f.get("provenance")
        if prov == "source_explicit":
            claim = conn.execute(
                "select id, claim_type, source_version_id from evidence_claim where id=%s", (f.get("claim_id"),)
            ).fetchone()
            if not claim:
                raise ProvenanceError("unknown_claim", f"{name}: claim {f.get('claim_id')} does not exist")
            if claim["claim_type"] != "dose":
                raise ProvenanceError("not_a_dose_claim", f"{name}: claim {claim['id']} is a {claim['claim_type']} claim, not a dose")
            if src_version and claim["source_version_id"] != src_version:
                raise ProvenanceError(
                    "claim_from_other_source",
                    f"{name}: claim {claim['id']} comes from a different source than the exercise it is attached to",
                )
        elif prov == "clinician_authored" and actor_id is not None:
            if str(f.get("author_id")) != str(actor_id):
                raise ProvenanceError("author_mismatch", f"{name}: clinician-authored value must name the person entering it")
