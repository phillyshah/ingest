-- 0014 domain-level source policy: the curated allowlist, and where a source's rights come from.
--
-- Until now the only allowlist was ALLOWED_FETCH_DOMAINS, an environment variable, and the only rights were
-- whatever a human typed into POST /sources. Both are wrong for directed research: the operator is asking the
-- system to find sources, so the system has to already know which publishers it may read and on what terms.
--
-- The unit is the domain, not the URL. A publisher licenses a site, not a page. A `source` row still carries its
-- own allowlist_state for one-off exceptions; the policy is what makes a whole publisher usable.
--
-- Three states are deliberately kept apart, because collapsing them is how a system ends up asserting rights
-- nobody checked:
--
--   license_id     what the publisher says the terms are     (a claim)
--   evidence       the terms page as actually fetched        (proof the claim was read)
--   review_state   a rights reviewer signed those terms      (a person accepted them)
--
-- A policy only permits anything when all three line up, which is what the generated `effective` column says.
-- Editing any of them drops the signature, the same way editing a clinical field invalidates approval.

create table source_policy (
  id uuid primary key default gen_random_uuid(),
  domain text not null unique,            -- exact lowercase host. No wildcards: a subdomain is its own decision.
  publisher text not null,
  license_id text not null,               -- key into the license catalogue in fixtures/source-policies/licenses.yaml
  policy_reference text not null,         -- the terms/licence page a human and the capture job both read
  scope_note text,                        -- what the licence does NOT cover, in the publisher's own framing

  -- Derived from the licence at load time, overridable per publisher. `unknown` blocks, everywhere, always.
  can_fetch                permission_state not null default 'unknown',
  can_store_fulltext       permission_state not null default 'unknown',
  can_store_excerpt        permission_state not null default 'unknown',
  can_embed_for_search     permission_state not null default 'unknown',
  can_process_with_model   permission_state not null default 'unknown',
  can_store_transcript     permission_state not null default 'unknown',
  can_download_media       permission_state not null default 'unknown',
  can_display_to_clinician permission_state not null default 'unknown',
  can_display_to_patient   permission_state not null default 'unknown',
  can_redistribute         permission_state not null default 'unknown',
  can_transform            permission_state not null default 'unknown',
  can_train_model          permission_state not null default 'denied',

  attribution_required boolean,
  attribution_text text,
  territory text,

  -- Evidence: the terms page as fetched, not as remembered. `sha256` is of the extracted text, so a layout or
  -- advertising change does not read as a licence change.
  evidence jsonb not null default '{}'::jsonb,   -- {fetched_at, sha256, http_status, quoted_span, final_url}
  evidence_state text not null default 'uncaptured'
    check (evidence_state in ('uncaptured','captured','drifted','unreachable')),

  review_state text not null default 'pending' check (review_state in ('pending','signed','rejected')),
  reviewed_by uuid references app_user(id),
  reviewed_at timestamptz,
  review_note text,
  -- What the signature actually covers. If the terms page changes, this no longer matches and the policy stops
  -- being effective without anyone having to notice.
  signed_evidence_sha256 text,

  review_cadence_days int not null default 180,
  next_review_at timestamptz,

  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),

  -- A signature is meaningless without evidence to sign.
  check (review_state <> 'signed' or signed_evidence_sha256 is not null)
);

comment on table source_policy is
  'Curated per-domain publishing terms. A domain is only fetchable when a rights reviewer has signed the exact terms text that was fetched from policy_reference.';

-- The single question the fetch gate asks. Generated rather than computed in application code so that no caller
-- can accidentally treat a pending or drifted policy as usable.
alter table source_policy add column effective boolean
  generated always as (
    review_state = 'signed'
    and signed_evidence_sha256 is not null
    and signed_evidence_sha256 = (evidence ->> 'sha256')
    and can_fetch = 'allowed'
  ) stored;

comment on column source_policy.effective is
  'True only when the policy is signed, the signature covers the evidence currently on record, and fetching is permitted. Anything else blocks.';

create index source_policy_effective_idx on source_policy (domain) where effective;
create index source_policy_review_idx on source_policy (review_state, next_review_at);

create trigger source_policy_touch before update on source_policy for each row execute function touch_updated_at();

-- Changing the terms, the licence, or any permission invalidates the signature. Same rule as clinical approval:
-- a person signed a specific thing, and this is no longer that thing.
create or replace function source_policy_invalidate_signature() returns trigger language plpgsql as $$
declare
  changed boolean := false;
  op      text;
begin
  if new.license_id is distinct from old.license_id
     or new.policy_reference is distinct from old.policy_reference
     or (new.evidence ->> 'sha256') is distinct from (old.evidence ->> 'sha256') then
    changed := true;
  end if;

  foreach op in array array[
    'can_fetch','can_store_fulltext','can_store_excerpt','can_embed_for_search','can_process_with_model',
    'can_store_transcript','can_download_media','can_display_to_clinician','can_display_to_patient',
    'can_redistribute','can_transform','can_train_model'
  ] loop
    if to_jsonb(new) ->> op is distinct from to_jsonb(old) ->> op then
      changed := true;
    end if;
  end loop;

  -- A signing update is the one case where the reviewer is deliberately re-signing what just changed.
  if changed and new.review_state = 'signed' and old.review_state = 'signed'
     and new.signed_evidence_sha256 is not distinct from old.signed_evidence_sha256 then
    new.review_state := 'pending';
    new.reviewed_by := null;
    new.reviewed_at := null;
    new.signed_evidence_sha256 := null;
    new.review_note := 'signature invalidated: the terms or permissions changed after signing';
  end if;
  return new;
end $$;

create trigger source_policy_invalidate before update on source_policy
  for each row execute function source_policy_invalidate_signature();

-- Which policy a rights grant was materialised from. Without this, a grant is an assertion with no provenance,
-- and re-verifying a publisher's terms could not find the records that depend on them.
alter table rights_grant add column source_policy_id uuid references source_policy(id);
create index rights_grant_policy_idx on rights_grant (source_policy_id);

comment on column rights_grant.source_policy_id is
  'The domain policy this grant was derived from, when it was not entered by hand. Null means a human supplied the permissions directly.';

-- Supabase exposure boundary (see 0010/0012): every table in public carries RLS and at least one policy, or
-- verify_supabase.py fails the deployment.
alter table source_policy enable row level security;
do $$
begin
  if exists (select 1 from pg_roles where rolname = 'moveai_app') then
    create policy backend_all on source_policy for all to moveai_app using (true) with check (true);
  else
    raise exception 'role moveai_app is missing; refusing to create a table with RLS on and no policy';
  end if;
end $$;
