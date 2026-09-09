-- 0005 patient store: tenant-scoped, RLS enforced, composite tenant FKs. Spec §6, §8, §19. No PHI in catalog.
create table case_snapshot (
  id uuid not null default gen_random_uuid(),
  tenant_id uuid not null references tenant(id),
  case_ref text not null,                              -- pseudonymous case ID from the tenant
  revision int not null default 1,
  assessment_time timestamptz,
  assessor_user_id uuid references app_user(id),
  narrative text,                                      -- raw input; proposed data until PT confirms
  intake jsonb not null default '{}'::jsonb,           -- {field: {status, value, unit?, provenance, confirmed_by?}}
  completeness jsonb not null default '{}'::jsonb,
  created_at timestamptz not null default now(),
  primary key (id),
  unique (tenant_id, id),
  unique (tenant_id, case_ref, revision)
);

create table case_observation (
  id uuid primary key default gen_random_uuid(),
  tenant_id uuid not null,
  case_snapshot_id uuid not null,
  dimension text not null,                             -- age|height|weight|bmi|sex|gender|comorbidity|function|...
  value jsonb,
  unit text,
  status field_status not null,
  measured_at timestamptz,
  method text,                                         -- measured|self_reported|derived|narrative_extracted
  provenance text not null,
  classification_version_id uuid references segmentation_definition_version(id),
  classified_value text,
  created_at timestamptz not null default now(),
  foreign key (tenant_id, case_snapshot_id) references case_snapshot(tenant_id, id)
);
create index on case_observation (case_snapshot_id);

create table plan_version (
  id uuid not null default gen_random_uuid(),
  tenant_id uuid not null references tenant(id),
  plan_id uuid not null,                               -- stable across revisions
  revision int not null default 1,
  case_snapshot_id uuid not null,
  catalog_release_id uuid references catalog_release(id),
  status plan_version_status not null default 'draft',
  routing plan_status not null,
  option_id text,
  protocol_version_ids uuid[] not null default '{}',
  rule_version_ids uuid[] not null default '{}',
  options jsonb not null default '[]'::jsonb,          -- full option payloads as returned by /plan-options
  selected_items jsonb not null default '[]'::jsonb,   -- [{variant_version_id, clinical_use_version_id, media_version_id?, prescribed_dose, goal_ids, evidence_claim_ids, rationale}]
  monitoring_rules jsonb not null default '[]'::jsonb,
  reassessment jsonb,
  progression_requirements jsonb not null default '[]'::jsonb,
  validation jsonb not null default '{}'::jsonb,       -- {passed, blockers[], warnings[]}
  rationale text,
  segment_explanations jsonb not null default '[]'::jsonb,
  content_hash text not null,
  author_id uuid references app_user(id),
  approved_by uuid references app_user(id),
  approved_at timestamptz,
  approval_signature text,                             -- HMAC over (case_snapshot_id, content_hash, catalog_release_id, revision)
  review_required_reason text,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  primary key (id),
  unique (tenant_id, id),
  unique (tenant_id, plan_id, revision),
  foreign key (tenant_id, case_snapshot_id) references case_snapshot(tenant_id, id),
  check (status <> 'approved' or (approved_by is not null and approval_signature is not null))
);
create index on plan_version (tenant_id, plan_id, revision desc);

-- Approved plans are immutable except review_required/superseded/withdrawn transitions.
create or replace function enforce_plan_immutability() returns trigger language plpgsql as $$
begin
  if old.status = 'approved' then
    if new.status not in ('approved','review_required','superseded','withdrawn') then
      raise exception 'immutable_version: approved plan % cannot become %', old.id, new.status using errcode='P0001';
    end if;
    if (to_jsonb(old) - 'status' - 'review_required_reason' - 'updated_at') <> (to_jsonb(new) - 'status' - 'review_required_reason' - 'updated_at') then
      raise exception 'immutable_version: approved plan % cannot be edited; create a new revision', old.id using errcode='P0001';
    end if;
  end if;
  new.updated_at := now();
  return new;
end $$;
create trigger plan_immutable before update on plan_version for each row execute function enforce_plan_immutability();

-- RLS: tenant-scoped tables only visible for the current tenant GUC. Service role bypasses only via BYPASSRLS.
alter table case_snapshot enable row level security;
alter table case_observation enable row level security;
alter table plan_version enable row level security;
alter table case_snapshot force row level security;
alter table case_observation force row level security;
alter table plan_version force row level security;
create policy tenant_isolation on case_snapshot using (tenant_id = current_tenant()) with check (tenant_id = current_tenant());
create policy tenant_isolation on case_observation using (tenant_id = current_tenant()) with check (tenant_id = current_tenant());
create policy tenant_isolation on plan_version using (tenant_id = current_tenant()) with check (tenant_id = current_tenant());

-- Application role used by API/workers: subject to RLS. Created idempotently.
do $$ begin
  if not exists (select 1 from pg_roles where rolname = 'moveai_app') then
    create role moveai_app nologin;
  end if;
end $$;
grant usage on schema public to moveai_app;
grant select, insert, update, delete on all tables in schema public to moveai_app;
alter default privileges in schema public grant select, insert, update, delete on tables to moveai_app;
