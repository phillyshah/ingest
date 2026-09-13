"""Normalize stage: concept matching, draft variant versions, duplicate *proposals* (never automatic merges),
evidence claims with locators (spec §5.6-5.8)."""

from __future__ import annotations

import re
import uuid
from typing import Any

import psycopg
from moveai_db import J

from .llm import ExtractionResult

ASSIST_WORDS = {
    "passive": "passive",
    "active-assisted": "active_assisted",
    "active assisted": "active_assisted",
    "assisted": "active_assisted",
    "resisted": "resisted",
    "active": "active",
}


def _slug(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", s.lower()).strip()


def infer_assistance(name: str, stated: str | None) -> str | None:
    if stated in ("passive", "active_assisted", "active", "resisted"):
        return stated
    n = name.lower()
    for k in ("active-assisted", "active assisted", "assisted", "resisted", "passive", "active"):
        if k in n:
            return ASSIST_WORDS[k]
    return None


def match_concept(conn: psycopg.Connection, name: str) -> dict | None:
    base = _slug(re.sub(r"\(.*?\)", "", name))
    for row in conn.execute("select id, preferred_name, aliases from exercise_concept").fetchall():
        names = [row["preferred_name"], *row["aliases"]]
        if any(_slug(n) == base or _slug(n) in base or base in _slug(n) for n in names if n):
            return row
    return None


def ensure_concept(conn: psycopg.Connection, name: str, region: str) -> dict:
    found = match_concept(conn, name)
    if found:
        return found
    clean = re.sub(r"\(.*?\)", "", name).strip()
    return conn.execute(
        "insert into exercise_concept(preferred_name, body_region) values (%s,%s) returning id, preferred_name, aliases",
        (clean, region),
    ).fetchone()


def propose_duplicate(conn: psycopg.Connection, concept_id: Any, assistance: str, setting: list[str], equipment: list[str]) -> Any | None:
    """A candidate duplicate needs the same concept AND same assistance/setting/equipment; otherwise it is distinct."""
    rows = conn.execute(
        "select entity_id, assistance, setting, equipment from exercise_variant_version where concept_id=%s and approval_state <> 'rejected'",
        (concept_id,),
    ).fetchall()
    for r in rows:
        if r["assistance"] == assistance and sorted(r["setting"]) == sorted(setting) and sorted(r["equipment"]) == sorted(equipment):
            return r["entity_id"]
    return None


def persist(
    conn: psycopg.Connection,
    *,
    source_version_id: Any,
    source_row: dict,
    result: ExtractionResult,
    review_flags: list[str],
    region: str = "unknown",
    excerpt_allowed: bool = False,
    tenant_id: Any = None,
    extracted_images: list[dict[str, Any]] | None = None,
    page_text: list[dict[str, Any]] | None = None,
    rights_grant_id: Any = None,
) -> dict[str, Any]:
    created_variants: list[Any] = []
    created_claims: list[Any] = []
    duplicates: list[dict[str, Any]] = []
    created_by_name: list[tuple[Any, str]] = []  # for embedded-image association below; never touches the model
    for ex in result.exercises:
        assistance = infer_assistance(ex.source_exercise_name, ex.assistance)
        if assistance is None:
            review_flags.append(f"{ex.source_exercise_name}: cannot determine assistance; needs review")
            continue
        concept = ensure_concept(conn, ex.source_exercise_name, region)
        dup = propose_duplicate(conn, concept["id"], assistance, ex.setting, ex.equipment)
        src_ref = {
            "url": source_row["canonical_url"],
            "publisher": source_row.get("publisher"),
            "title": source_row.get("title"),
            "document_version": result.document_identity,
            "locator": ex.locator,
            "source_exercise_name": ex.source_exercise_name,
        }
        entity_id = uuid.uuid4()
        vid = conn.execute(
            """insert into exercise_variant_version(concept_id, entity_id, version, tenant_id, name, region, assistance, setting, equipment,
                 step_sequence, source_reference, extraction_warnings, duplicate_of_entity_id, approval_state)
               values (%s,%s,1,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,'pending_review') returning id""",
            (
                concept["id"],
                entity_id,
                tenant_id,
                ex.source_exercise_name,
                region,
                assistance,
                ex.setting or ["home"],
                ex.equipment,
                J([{"step": i + 1, "text": s} for i, s in enumerate(ex.steps)]),
                J(src_ref),
                J([f for f in review_flags if f.startswith(ex.source_exercise_name)]),
                dup,
            ),
        ).fetchone()["id"]
        created_variants.append(vid)
        created_by_name.append((vid, ex.source_exercise_name))
        conn.execute(
            "insert into dependency_edge(upstream_table, upstream_id, downstream_table, downstream_id, dependency_type) values ('source_version',%s,'exercise_variant_version',%s,'derived_from')",
            (source_version_id, vid),
        )
        if dup:
            duplicates.append({"variant_version_id": str(vid), "duplicate_of_entity_id": str(dup)})
        # instruction claim
        cid = conn.execute(
            """insert into evidence_claim(source_version_id, locator, extraction_method, excerpt, paraphrase, claim_type)
               values (%s,%s,'llm',%s,%s,'exercise_instruction') returning id""",
            (
                source_version_id,
                J(ex.locator),
                "\n".join(ex.steps) if excerpt_allowed else None,
                f"Instructions for {ex.source_exercise_name} ({len(ex.steps)} steps)",
            ),
        ).fetchone()["id"]
        created_claims.append(cid)
        conn.execute(
            "insert into dependency_edge(upstream_table, upstream_id, downstream_table, downstream_id, dependency_type) values ('evidence_claim',%s,'exercise_variant_version',%s,'supported_by')",
            (cid, vid),
        )
        # dose claims: one per stated field, locator required
        for name, f in ex.dose.items():
            if f.value is None:
                continue
            dcid = conn.execute(
                """insert into evidence_claim(source_version_id, locator, extraction_method, paraphrase, claim_type, ocr_confidence, ambiguity_flags)
                   values (%s,%s,%s,%s,'dose',%s,%s) returning id""",
                (
                    source_version_id,
                    J(f.locator),
                    "ocr" if f.ocr_confidence is not None else "llm",
                    f"{ex.source_exercise_name}: {name} = {f.value}{' ' + f.unit if f.unit else ''}"
                    + (f" (phase: {ex.phase})" if ex.phase else " (phase: unlinked)"),
                    f.ocr_confidence,
                    ["phase_unlinked"] if ex.phase is None else [],
                ),
            ).fetchone()["id"]
            created_claims.append(dcid)
        for g in ex.graphics:
            # A referenced-but-unfetched graphic still hangs off this source's rights grant: without the link, a
            # later rights denial or expiry (reviews_service, propagate_withdrawal) could never find it.
            conn.execute(
                """insert into media_asset_version(entity_id, version, variant_version_id, media_type, media_state, url, rights_grant_id, approval_state)
                   values (%s,1,%s,'still_graphic',%s,%s,%s,'draft')""",
                (uuid.uuid4(), vid, "rights_hold" if g.third_party else "reference_only", g.src, rights_grant_id),
            )
    for note in result.clinician_only:
        conn.execute(
            "insert into evidence_claim(source_version_id, locator, extraction_method, paraphrase, claim_type) values (%s,%s,'llm',%s,'clinician_only_intervention')",
            (source_version_id, J(note.locator or {}), note.text[:500]),
        )
    for note in result.contraindications:
        conn.execute(
            "insert into evidence_claim(source_version_id, locator, extraction_method, paraphrase, claim_type) values (%s,%s,'llm',%s,'contraindication')",
            (source_version_id, J(note.locator or {}), note.text[:500]),
        )
    image_result = _associate_images(conn, created_by_name, extracted_images or [], page_text or [], rights_grant_id)
    return {
        "variants": [str(v) for v in created_variants],
        "claims": [str(c) for c in created_claims],
        "duplicates": duplicates,
        "media": image_result,
    }


def _find_pages(page_text: list[dict[str, Any]], name: str) -> set[int]:
    needle = _slug(name)
    if not needle:
        return set()
    return {row["page"] for row in page_text if needle in _slug(row["text"])}


def _associate_images(
    conn: psycopg.Connection,
    created_by_name: list[tuple[Any, str]],
    extracted_images: list[dict[str, Any]],
    page_text: list[dict[str, Any]],
    rights_grant_id: Any,
) -> dict[str, Any]:
    """Link an embedded PDF image to the one exercise on its page, or flag it for a human — never guess.

    The only signal used is which page the parser found the exercise's own name on: real page numbers from the
    document, computed here in code. Nothing from the extraction model feeds this, because the model was never
    shown the images and has no honest way to say which exercise a photo belongs to (spec §5).

    A page with exactly one exercise on it and one or more images: link them all. Anything else — no exercise
    found on that page, or more than one candidate — is not a guess this makes; it is recorded as a review flag
    on every candidate (or, with no candidate at all, returned so the caller can surface it at the job level) so a
    person attaches it by hand instead of the system asserting a pairing nobody confirmed.
    """
    if not extracted_images:
        return {"linked": 0, "unmatched_images": 0}
    variant_pages = {vid: _find_pages(page_text, name) for vid, name in created_by_name}
    images_by_page: dict[int, list[dict[str, Any]]] = {}
    for img in extracted_images:
        images_by_page.setdefault(img["page"], []).append(img)

    linked = 0
    unmatched: list[dict[str, Any]] = []
    for page, imgs in images_by_page.items():
        candidates = [vid for vid, pages in variant_pages.items() if page in pages]
        if len(candidates) == 1:
            vid = candidates[0]
            for img in imgs:
                conn.execute(
                    """insert into media_asset_version(entity_id, version, variant_version_id, media_type, media_state,
                            storage_ref, content_sha256, byte_size, rights_grant_id, approval_state)
                       values (%s,1,%s,'still_graphic','graphic_available',%s,%s,%s,%s,'draft')""",
                    (uuid.uuid4(), vid, img["storage_ref"], img["sha256"], img["byte_size"], rights_grant_id),
                )
            linked += len(imgs)
            continue
        # Ambiguous (0 or 2+ exercises share this page): attach the flag to every candidate's own
        # extraction_warnings, an UPDATE that only ever runs before a variant is ever approved.
        note = f"{len(imgs)} image(s) on page {page} could not be uniquely matched to one exercise; attach manually"
        if candidates:
            conn.execute(
                "update exercise_variant_version set extraction_warnings = extraction_warnings || %s where id = any(%s)",
                (J([note]), candidates),
            )
        else:
            unmatched.append({"page": page, "count": len(imgs), "note": note})
    return {"linked": linked, "unmatched_images": unmatched}
