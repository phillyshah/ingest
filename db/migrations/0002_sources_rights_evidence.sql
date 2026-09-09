-- 0002 sources, versions, rights, evidence. Spec §4, §5, §6.
create table source (
  id uuid primary key default gen_random_uuid(),
  tenant_id uuid references tenant(id),                -- null = shared catalog source
  canonical_url text not null,
  publisher text,
  title text,
  source_type text not null check (source_type in ('html','pdf','scanned_pdf','clinician_upload','sitemap','feed','api')),
  owner_user_id uuid references app_user(id),
  allowlist_state text not null default 'pending' check (allowlist_state in ('pending','approved','denied')),
  policy_reference text,
  review_cadence_days int,
  next_review_at timestamptz,
  crawl_limits jsonb not null default '{}'::jsonb,
  intended_uses text[] not null default '{}',
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  unique (canonical_url)
);
create trigger source_touch before update on source for each row execute function touch_updated_at();

create table source_version (
  id uuid primary key default gen_random_uuid(),
  source_id uuid not null references source(id),
  content_sha256 text,
  retrieved_at timestamptz,
  final_url text,
  content_type text,
  etag text,
  last_modified text,
  declared_publication_date date,
  document_identity text,                              -- e.g. title + version string from the doc itself
  storage_ref text,                                    -- object-storage pointer when can_store_fulltext=allowed
  byte_size int,
  pipeline_state pipeline_state not null default 'discovered',
  parser_version text,
  parse_warnings jsonb not null default '[]'::jsonb,
  supersedes_id uuid references source_version(id),
  superseded_by_id uuid references source_version(id),
  withdrawn_at timestamptz,
  withdrawal_reason text,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  unique (source_id, content_sha256)
);
create index on source_version (source_id, created_at desc);
create trigger source_version_touch before update on source_version for each row execute function touch_updated_at();

-- One rights grant per (source_version | media asset version). Every operation is tri-state; unknown blocks.
create table rights_grant (
  id uuid primary key default gen_random_uuid(),
  source_version_id uuid references source_version(id),
  media_asset_version_id uuid,                         -- FK added in 0003
  can_fetch permission_state not null default 'unknown',
  can_store_fulltext permission_state not null default 'unknown',
  can_store_excerpt permission_state not null default 'unknown',
  can_embed_for_search permission_state not null default 'unknown',
  can_process_with_model permission_state not null default 'unknown',
  can_store_transcript permission_state not null default 'unknown',
  can_download_media permission_state not null default 'unknown',
  can_display_to_clinician permission_state not null default 'unknown',
  can_display_to_patient permission_state not null default 'unknown',
  can_redistribute permission_state not null default 'unknown',
  can_transform permission_state not null default 'unknown',
  can_train_model permission_state not null default 'denied',
  attribution_required boolean,
  attribution_text text,
  territory text,
  expires_at timestamptz,
  revoked_at timestamptz,
  permission_evidence jsonb not null default '{}'::jsonb,   -- {kind, url|text, captured_at}
  rights_reviewer_id uuid references app_user(id),
  reviewed_at timestamptz,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  check (source_version_id is not null or media_asset_version_id is not null)
);
create index on rights_grant (source_version_id);
create trigger rights_grant_touch before update on rights_grant for each row execute function touch_updated_at();

create table evidence_group (
  id uuid primary key default gen_random_uuid(),
  label text not null,
  description text,
  created_at timestamptz not null default now()
);

-- Source locator shared shape: {page, section, anchor, table, row, char_start, char_end, timecode, quoted_span}
create table evidence_claim (
  id uuid primary key default gen_random_uuid(),
  source_version_id uuid not null references source_version(id),
  locator jsonb not null,
  extraction_method text not null check (extraction_method in ('rule','llm','clinician','ocr')),
  excerpt text,                                        -- only when can_store_excerpt=allowed
  paraphrase text,
  claim_type text not null check (claim_type in (
    'exercise_instruction','protocol_phase','contraindication','study_claim','education','clinician_only_intervention','dose','population')),
  population jsonb,                                    -- study eligibility/exclusions/demographics as reported
  intervention text,
  comparator text,
  outcome text,
  recommendation_strength_as_reported text,
  limitations text,
  evidence_group_id uuid references evidence_group(id),
  conflicts_with_claim_id uuid references evidence_claim(id),
  ocr_confidence numeric(4,3),
  ambiguity_flags text[] not null default '{}',
  created_at timestamptz not null default now()
);
create index on evidence_claim (source_version_id);
