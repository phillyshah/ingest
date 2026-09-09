-- 0004 rules, protocols, segmentation, applicability, catalog releases. Spec §6, §7, §19.
create table rule_version (
  id uuid primary key default gen_random_uuid(),
  entity_id uuid not null,
  version int not null,
  tenant_id uuid references tenant(id),
  name text not null,
  rule_kind text not null check (rule_kind in (
    'concern_screen','restriction','eligibility','progression','regression','symptom_limit','next_day_response','population_modifier','required_input')),
  expression jsonb not null,                           -- typed AST (packages/clinical_rules/ast.py), validated in app
  expression_schema_version int not null default 1,
  required_inputs text[] not null default '{}',
  action jsonb not null,                               -- {type: require_field|block_for_review|exclude_variant|inform|rank|monitor|adapt, ...}
  severity text not null default 'info' check (severity in ('info','warning','block')),
  rationale text,
  evidence_claim_ids uuid[] not null default '{}',
  author_id uuid references app_user(id),
  approver_id uuid references app_user(id),
  approval_state approval_state not null default 'draft',
  approved_at timestamptz,
  invalidated_at timestamptz, withdrawn_at timestamptz, withdrawal_reason text, superseded_by_id uuid,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  unique (entity_id, version),
  check (author_id is null or approver_id is null or author_id <> approver_id)
);
create trigger rule_immutable before update on rule_version for each row execute function enforce_version_immutability();
alter table variant_relationship add constraint variant_rel_rule_fk foreign key (rule_version_id) references rule_version(id);

create table protocol_version (
  id uuid primary key default gen_random_uuid(),
  entity_id uuid not null,
  version int not null,
  prior_version_id uuid references protocol_version(id),
  tenant_id uuid references tenant(id),                -- tenant-private unless null
  content_pack text,                                   -- fixtures/content-packs/<name>
  content_pack_version text,
  condition_id uuid not null references condition(id),
  name text not null,
  population_description text,
  author_id uuid references app_user(id),
  provenance jsonb not null default '{}'::jsonb,        -- {kind: source_family|clinician_authored|demo_synthetic, refs[]}
  setting text[] not null default '{}',
  required_inputs text[] not null default '{}',        -- intake fields that must be known before individual prescribing
  phases jsonb not null default '[]'::jsonb,           -- [{key, name, goals[], window{anchor, start, end, unit, provisional}, entry_criteria_rule_ids[], exit_criteria_rule_ids[], monitoring[], items[{clinical_use_entity_id}]}]
  monitoring jsonb not null default '[]'::jsonb,
  rule_version_ids uuid[] not null default '{}',
  recovery_horizon jsonb,                              -- {text, provenance} — uncertainty preserved
  approval_state approval_state not null default 'draft',
  approved_by uuid references app_user(id),
  approved_at timestamptz,
  invalidated_at timestamptz, withdrawn_at timestamptz, withdrawal_reason text,
  superseded_by_id uuid references protocol_version(id),
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  unique (entity_id, version),
  check (author_id is null or approved_by is null or author_id <> approved_by)
);
create index on protocol_version (condition_id, approval_state);
create trigger protocol_immutable before update on protocol_version for each row execute function enforce_version_immutability();

create table protocol_item (
  protocol_version_id uuid not null references protocol_version(id),
  phase_key text not null,
  clinical_use_version_id uuid not null references clinical_use_version(id),
  position int not null default 0,
  primary key (protocol_version_id, phase_key, clinical_use_version_id)
);

-- §19 segmentation
create table segmentation_definition_version (
  id uuid primary key default gen_random_uuid(),
  dimension text not null,                             -- age|bmi|sex|gender|physiology|comorbidity|function|presentation|environment
  authority text not null,                             -- e.g. CDC adult BMI categories
  authority_version text not null,
  population_scope text not null,                      -- e.g. adults >= 20 years
  boundaries jsonb not null,                           -- [{class, min, max, min_inclusive, max_inclusive, unit}]
  derivation text,
  effective_start date, effective_end date,
  source_url text,
  is_local_band boolean not null default false,        -- local reporting bands must be labeled local
  approval_state approval_state not null default 'draft',
  invalidated_at timestamptz, withdrawn_at timestamptz, withdrawal_reason text, superseded_by_id uuid,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  unique (dimension, authority, authority_version)
);
create trigger segdef_immutable before update on segmentation_definition_version for each row execute function enforce_version_immutability();

create table population_applicability_version (
  id uuid primary key default gen_random_uuid(),
  target_version_id uuid not null,
  target_type text not null check (target_type in ('clinical_use','protocol')),
  predicate_logic text not null default 'and' check (predicate_logic in ('and','or')),
  relationship_type applicability_relationship not null,
  evidence_claim_ids uuid[] not null default '{}',
  source_locator_ids uuid[] not null default '{}',
  support_category support_category,
  generalizability_limits text,
  subgroup_results jsonb,
  action_type applicability_action not null default 'inform_only',
  executable_rule_version_id uuid references rule_version(id),
  clinician_rationale text,
  reviewer_id uuid references app_user(id),
  approval_state approval_state not null default 'draft',
  review_due_at timestamptz,
  invalidated_at timestamptz, withdrawn_at timestamptz, withdrawal_reason text, superseded_by_id uuid,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  -- only an executable rule may adapt/monitor/exclude (§19)
  check (action_type in ('inform_only','rank','request_assessment') or executable_rule_version_id is not null)
);
create trigger popapp_immutable before update on population_applicability_version for each row execute function enforce_version_immutability();

create table population_predicate (
  id uuid primary key default gen_random_uuid(),
  applicability_id uuid not null references population_applicability_version(id) on delete cascade,
  dimension text not null,
  operator text not null check (operator in ('eq','ne','lt','lte','gt','gte','between','in','not_in','reported','not_reported')),
  value jsonb,
  unit text,
  min_inclusive boolean, max_inclusive boolean,
  classification_definition_version_id uuid references segmentation_definition_version(id)
);

-- catalog releases (§5.10, §6)
create table catalog_release (
  id uuid primary key default gen_random_uuid(),
  label text not null unique,
  manifest jsonb not null,                             -- [{table, version_id, content_hash}]
  manifest_sha256 text not null,
  published_by uuid references app_user(id),
  published_at timestamptz not null default now(),
  prior_release_id uuid references catalog_release(id)
);
create table catalog_release_item (
  release_id uuid not null references catalog_release(id),
  table_name text not null,
  version_id uuid not null,
  content_hash text not null,
  primary key (release_id, table_name, version_id)
);
create index on catalog_release_item (version_id);

create table dependency_edge (
  id uuid primary key default gen_random_uuid(),
  upstream_table text not null, upstream_id uuid not null,
  downstream_table text not null, downstream_id uuid not null,
  dependency_type dependency_type not null,
  created_at timestamptz not null default now(),
  unique (upstream_table, upstream_id, downstream_table, downstream_id, dependency_type)
);
create index on dependency_edge (upstream_id);
create index on dependency_edge (downstream_id);
