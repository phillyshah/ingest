-- Review-found fixes (September 2026 backend review).
--
-- 1. A technique-review decision. `media_asset_version.technique_review_passed` has existed since 0003 and the
--    planner has always required it before a picture may be shown to a patient (spec §20A) — but nothing ever set
--    it, so no photo, however well-licensed, could reach a plan. The decision is recorded like every other review
--    decision, with the reviewer's identity.
-- 2. Author ≠ approver at the database for variants and clinical uses, matching the CHECK that rules and protocols
--    already carry (0004). The application check in reviews_service only runs through the API; this backs it.
--    Ingested rows deliberately carry created_by = NULL: an extraction is machine-authored, and the first human
--    touch is the reviewer — so the constraint only bites when a person authored or edited the version.

alter type review_decision add value if not exists 'technique_review';

alter table exercise_variant_version
  add constraint exercise_variant_author_not_approver
  check (approved_by is null or created_by is null or approved_by <> created_by);

alter table clinical_use_version
  add constraint clinical_use_author_not_approver
  check (approved_by is null or created_by is null or approved_by <> created_by);
