-- 0003 exercise concepts, variants, clinical uses, media, conditions, terminology. Spec §6.
create table exercise_concept (
  id uuid primary key default gen_random_uuid(),
  preferred_name text not null unique,
  aliases text[] not null default '{}',
  body_region text not null,
  movement_purpose text,
  created_at timestamptz not null default now()
);

create table exercise_variant_version (
  id uuid primary key default gen_random_uuid(),
  concept_id uuid not null references exercise_concept(id),
  entity_id uuid not null,                             -- stable across versions
  version int not null,
  prior_version_id uuid references exercise_variant_version(id),
  tenant_id uuid references tenant(id),                -- null = shared catalog
  name text not null,
  region text not null,
  joint text,
  movement_plane text,
  target_impairment text[] not null default '{}',
  functional_goal text[] not null default '{}',
  starting_position text,
  assistance assistance_mode not null,
  chain text check (chain in ('open','closed')),
  load_mode text,
  side_behavior text check (side_behavior in ('unilateral','bilateral','alternating','not_applicable')),
  equipment text[] not null default '{}',
  balance_demand text,
  accessibility_notes text,
  setting text[] not null default '{}' check (setting <@ array['home','supervised']::text[]),
  step_sequence jsonb not null default '[]'::jsonb,   -- [{step, text, claim_id?}]
  breathing_cues text,
  common_errors text[] not null default '{}',
  contraindication_claim_ids uuid[] not null default '{}',
  supported_modifications jsonb not null default '[]'::jsonb,
  range_constraints jsonb,
  cues text[] not null default '{}',
  supported_languages text[] not null default '{en}',
  source_reference jsonb,                              -- {url, publisher, title, document_version, locator, source_exercise_name}
  extraction_warnings jsonb not null default '[]'::jsonb,
  duplicate_of_entity_id uuid,                         -- proposed duplicate; review resolves
  approval_state approval_state not null default 'draft',
  approved_by uuid references app_user(id),
  approved_at timestamptz,
  invalidated_at timestamptz,
  withdrawn_at timestamptz,
  withdrawal_reason text,
  superseded_by_id uuid references exercise_variant_version(id),
  created_by uuid references app_user(id),
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  unique (entity_id, version)
);
create index on exercise_variant_version (concept_id);
create index on exercise_variant_version (approval_state);
create trigger variant_immutable before update on exercise_variant_version
  for each row execute function enforce_version_immutability();

-- Progressions/regressions are explicit relationships with criteria (§6).
create table variant_relationship (
  id uuid primary key default gen_random_uuid(),
  from_entity_id uuid not null,
  to_entity_id uuid not null,
  relationship text not null check (relationship in ('progression','regression','accessible_alternative')),
  criteria jsonb not null default '{}'::jsonb,
  rule_version_id uuid,                                -- FK in 0004
  created_at timestamptz not null default now(),
  unique (from_entity_id, to_entity_id, relationship)
);

create table condition (
  id uuid primary key default gen_random_uuid(),
  internal_code text not null unique,                  -- e.g. adhesive_capsulitis
  preferred_name text not null,
  synonyms text[] not null default '{}',
  body_region text not null,
  ambiguity_flags text[] not null default '{}',
  created_at timestamptz not null default now()
);

create table terminology_release (
  id uuid primary key default gen_random_uuid(),
  system text not null,                                -- ICD-10-CM
  release_id text not null,                            -- FY26, FY27
  effective_start date not null,
  effective_end date,
  source_hash text,
  source_url text,
  imported_at timestamptz not null default now(),
  unique (system, release_id)
);

create table terminology_code (
  id uuid primary key default gen_random_uuid(),
  release_id uuid not null references terminology_release(id),
  code text not null,
  descriptor text not null,
  billable boolean not null default false,
  laterality text check (laterality in ('right','left','bilateral','unspecified','not_applicable')),
  unique (release_id, code)
);
create index on terminology_code (code);

create table diagnosis_mapping_version (
  id uuid primary key default gen_random_uuid(),
  condition_id uuid not null references condition(id),
  terminology_code_id uuid not null references terminology_code(id),
  relationship text not null check (relationship in ('exact','broader','narrower')),
  confidence text not null check (confidence in ('high','medium','low')),
  reviewer_id uuid references app_user(id),
  approval_state approval_state not null default 'draft',
  withdrawn_at timestamptz, withdrawal_reason text, superseded_by_id uuid, invalidated_at timestamptz,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  unique (condition_id, terminology_code_id)
);
create trigger diag_map_immutable before update on diagnosis_mapping_version
  for each row execute function enforce_version_immutability();

-- Dose schema (§6): every field is {value|range, unit, null_reason, provenance}. Stored as jsonb validated in app;
-- a check keeps the shape honest.
create or replace function dose_shape_ok(d jsonb) returns boolean language sql immutable as $$
  select d is null or (
    jsonb_typeof(d) = 'object' and
    not exists (
      select 1 from jsonb_each(d) f
      where jsonb_typeof(f.value) <> 'object'
         or not (f.value ? 'provenance')
         or not (f.value->>'provenance' in ('source_explicit','clinician_authored','not_applicable','unknown'))
         or ((f.value->'value' is null or jsonb_typeof(f.value->'value')='null')
             and (f.value->'range' is null or jsonb_typeof(f.value->'range')='null')
             and (f.value->>'null_reason') is null)
    )
  )
$$;

create table clinical_use_version (
  id uuid primary key default gen_random_uuid(),
  entity_id uuid not null,
  version int not null,
  prior_version_id uuid references clinical_use_version(id),
  tenant_id uuid references tenant(id),
  variant_version_id uuid not null references exercise_variant_version(id),
  condition_id uuid not null references condition(id),
  presentation jsonb not null default '{}'::jsonb,     -- {irritability, phase, procedure?, ...} descriptive
  indication text,
  exclusion text,
  phase text,
  goals text[] not null default '{}',
  dose_envelope jsonb check (dose_shape_ok(dose_envelope)),
  supporting_claim_ids uuid[] not null default '{}',
  directness text not null default 'unknown' check (directness in ('direct','indirect','unknown')),
  support_category support_category,
  clinician_rationale text,
  approval_state approval_state not null default 'draft',
  approved_by uuid references app_user(id),
  approved_at timestamptz,
  invalidated_at timestamptz, withdrawn_at timestamptz, withdrawal_reason text,
  superseded_by_id uuid references clinical_use_version(id),
  created_by uuid references app_user(id),
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  unique (entity_id, version)
);
create index on clinical_use_version (variant_version_id);
create index on clinical_use_version (condition_id, approval_state);
create trigger clinical_use_immutable before update on clinical_use_version
  for each row execute function enforce_version_immutability();

create table media_asset_version (
  id uuid primary key default gen_random_uuid(),
  entity_id uuid not null,
  version int not null,
  variant_version_id uuid not null references exercise_variant_version(id),
  media_type text not null check (media_type in ('still_graphic','video','animation')),
  media_state media_state not null default 'not_requested',
  url text,
  storage_ref text,                                    -- private bucket pointer; never bytes
  content_sha256 text,
  byte_size int, width_px int, height_px int,
  duration_seconds numeric,
  language text,
  captions_available boolean,
  laterality text,
  technique_review_passed boolean,
  technique_reviewer_id uuid references app_user(id),
  rights_grant_id uuid references rights_grant(id),
  rendering_spec jsonb,
  approval_state approval_state not null default 'draft',
  invalidated_at timestamptz, withdrawn_at timestamptz, withdrawal_reason text, superseded_by_id uuid,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  unique (entity_id, version)
);
create trigger media_immutable before update on media_asset_version
  for each row execute function enforce_version_immutability();
alter table rights_grant add constraint rights_grant_media_fk
  foreign key (media_asset_version_id) references media_asset_version(id);

-- join tables
create table clinical_use_evidence (
  clinical_use_version_id uuid not null references clinical_use_version(id),
  evidence_claim_id uuid not null references evidence_claim(id),
  directness text not null check (directness in ('direct','indirect')),
  primary key (clinical_use_version_id, evidence_claim_id)
);
