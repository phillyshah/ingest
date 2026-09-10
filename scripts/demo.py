"""End-to-end acceptance demonstration (spec §16 definition of done). Text-only; no graphics or videos stored."""

from __future__ import annotations

import json
import os
import sys
import uuid
from pathlib import Path

os.environ.setdefault("PLAN_SIGNING_SECRET", "dev-only-secret-change-me")
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "services" / "planner" / "tests"))

from moveai_contracts.enums import FieldStatus  # noqa: E402
from moveai_contracts.intake import CaseSubmission, Intake, IntakeField  # noqa: E402
from moveai_contracts.plan import DraftPatch  # noqa: E402
from moveai_db import connect  # noqa: E402
from moveai_planner.approval import approve, get_approved, patch_draft, propagate_withdrawal  # noqa: E402
from moveai_planner.engine import narrative_for, plan_options  # noqa: E402
from moveai_planner.seed import ensure_users  # noqa: E402

OUT = ROOT / ".demo-out"
OUT.mkdir(exist_ok=True)
CANONICAL = [
    "55-year-old slightly obese man with frozen shoulder",
    "Patient recovering after surgical MCL repair",
    "Patient with a hamstring pull, treated with physical therapy and no surgery",
]


def K(v, **kw):
    return IntakeField(status=FieldStatus.known, value=v, provenance="pt_entered", **kw)


def section(title: str) -> None:
    print("\n" + "=" * 8, title, "=" * 8)


def main() -> int:
    ok = True
    with connect() as conn:
        ctx = ensure_users(conn)
        rel = conn.execute("select id, label from catalog_release order by published_at desc limit 1").fetchone()
        if not rel:
            print("run `make seed` first")
            return 2
        # Section 4 exercises the approval path, which needs the clearly-labelled non-clinical demo pack. That pack
        # is excluded from a default seed on purpose, so say so rather than failing with an IndexError.
        if not conn.execute("select 1 from protocol_version where content_pack='demo_synthetic'").fetchone():
            print(
                "the demo_synthetic content pack is not installed.\n"
                "  It is excluded from a default seed because it is approved and can produce draft_ready plans.\n"
                "  Run: uv run python scripts/seed.py --with-demo-pack   (or `make demo`, which does it for you)"
            )
            return 2
        section("1. Catalog state (seeded fixtures + content packs)")
        for r in conn.execute("select approval_state, count(*) as n from exercise_variant_version group by 1 order by 1").fetchall():
            print(f"  variants {r['approval_state']:20} {r['n']}")
        print(
            "  media binaries stored:",
            conn.execute("select count(*) as n from media_asset_version where storage_ref is not null").fetchone()["n"],
            "(must be 0)",
        )
        holds = conn.execute("select count(*) as n from media_asset_version where media_state='rights_hold'").fetchone()["n"]
        print(f"  graphics on rights_hold: {holds} (text-only content still flows)")
        print("  pinned release:", rel["label"])

        section("2. Sparse inputs for all three families -> needs_assessment with conditional previews")
        for text in CANONICAL:
            r = plan_options(
                conn,
                tenant_id=ctx["tenant_id"],
                user_id=ctx["pt"],
                submission=CaseSubmission(case_ref=f"demo-{uuid.uuid4().hex[:6]}", narrative=text),
            )
            (OUT / f"plan-options-{text[:20].replace(' ', '_')}.json").write_text(r.model_dump_json(indent=2))
            print(f"  '{text}'")
            print(
                f"    status={r.status.value} assignable={r.assignable} prescription={r.prescription} previews={len(r.pathway_previews)} anchor={r.timeline_anchor}"
            )
            print(f"    missing: {[m.field for m in r.missing_fields]}")
            for p in r.pathway_previews[:1]:
                for row in p.timeline[:1]:
                    print(
                        f"    preview '{p.protocol_name}' [{p.approval_state}] phase '{row.phase_name}' window={row.time_window.text!r} exercises={[e.variant_name for e in row.exercises]}"
                    )
                    for e in row.exercises[:1]:
                        print(
                            f"      source ref: {e.source_references[0].publisher} / {e.source_references[0].title} locator={e.source_references[0].locator}"
                        )
            ok &= r.status.value == "needs_assessment" and r.prescription is None and not r.assignable and bool(r.pathway_previews)

        section("3. Complete frozen-shoulder assessment on the unsigned placeholder pack -> blocked (no approved pathway)")
        fs = {
            "affected_side": K("right"),
            "diagnosis_confirmation": K({"confirmed": True}),
            "irritability": K("low"),
            "rom_active": K({"flexion": 100}, unit="deg"),
            "rom_passive": K({"flexion": 110}, unit="deg"),
            "surgeon_restrictions": K("none"),
            "procedure": K("none"),
            "goals": K(["reach behind head"]),
            "prior_interventions": K([]),
            "concerning_findings": K([]),
            "age": K(55, unit="years"),
            "prior_session_response": IntakeField(status=FieldStatus.not_applicable),
        }
        r = plan_options(
            conn,
            tenant_id=ctx["tenant_id"],
            user_id=ctx["pt"],
            submission=CaseSubmission(case_ref="demo-fs-full", narrative="frozen shoulder", intake=Intake(fields=fs)),
        )
        print(f"  status={r.status.value}; blocked={r.blocked.rule_name if r.blocked else None}: {r.blocked.message if r.blocked else ''}")
        ok &= r.status.value == "blocked_for_clinical_review"

        section("4. Demo synthetic pack (approved, non-clinical) -> draft_ready -> PT edit -> approve -> adapter retrieval")
        demo = {
            "affected_side": K("left"),
            "concerning_findings": K([]),
            "surgeon_restrictions": K("none"),
            "equipment": K(["elastic band"]),
            "height": K(1.75, unit="m"),
            "weight": K(96, unit="kg"),
            "age": K(55, unit="years"),
            "prior_session_response": IntakeField(status=FieldStatus.not_applicable),
        }
        r = plan_options(
            conn,
            tenant_id=ctx["tenant_id"],
            user_id=ctx["pt"],
            submission=CaseSubmission(case_ref="demo-approve", narrative="demo condition", intake=Intake(fields=demo)),
        )
        print(f"  status={r.status.value} options={len(r.options)}")
        for s in r.segment_explanations:
            print(f"    segment {s.dimension:12} observed={s.observed!r} class={s.classification} effect={s.effect}")
        opt = r.options[0]
        print(
            f"  option '{opt.label}': items={[i.variant_name for i in opt.items]} validation.passed={opt.validation.passed} media={[i.media_state for i in opt.items]}"
        )
        rev2 = patch_draft(
            conn,
            tenant_id=ctx["tenant_id"],
            user_id=ctx["pt"],
            plan_id=r.plan_id,
            patch=DraftPatch(expected_revision=1, selected_option_id=opt.option_id, rationale=narrative_for(opt)),
        )
        approved = approve(
            conn,
            tenant_id=ctx["tenant_id"],
            user_id=ctx["pt"],
            roles=["pt"],
            plan_id=r.plan_id,
            expected_revision=rev2["revision"],
            attestation="demo",
        )
        got = get_approved(conn, tenant_id=ctx["tenant_id"], plan_id=r.plan_id)
        print(
            f"  approved revision={approved['revision']} signature={approved['approval_signature'][:16]}... adapter available={got['available']} hash_match={got['content_hash'] == approved['content_hash']}"
        )
        (OUT / "approved-plan.json").write_text(json.dumps(got, indent=2, default=str))
        ok &= got["available"]

        section("5. Clinical-source withdrawal impact")
        claim = opt.items[0].evidence_claim_ids[0]
        imp = propagate_withdrawal(conn, entity_table="evidence_claim", version_id=claim, reason="demo: source corrected")
        got2 = get_approved(conn, tenant_id=ctx["tenant_id"], plan_id=r.plan_id)
        print(f"  affected versions={len(imp['affected_versions'])} plans routed to review={imp['plans_routed_to_review']}")
        print(
            f"  adapter now: available={got2['available']} hold={got2['hold']} assignable={got2.get('assignable')} reason={got2['hold_reason']}"
        )
        ok &= got2["hold"] is True

        section("6. Duplicate prevention")
        dups = conn.execute("select count(*) as n from exercise_variant_version where duplicate_of_entity_id is not null").fetchone()["n"]
        print(f"  proposed duplicates awaiting review: {dups} (never auto-merged)")
        conn.rollback()  # the demo leaves the seeded database unchanged
    section("RESULT")
    print("  ACCEPTANCE DEMO", "PASSED" if ok else "FAILED", f"(artifacts in {OUT})")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
