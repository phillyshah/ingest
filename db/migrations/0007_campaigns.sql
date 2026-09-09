-- 0007 administrator campaigns. Spec §21F.
create table ingestion_campaign (
  id uuid primary key default gen_random_uuid(),
  tenant_id uuid not null references tenant(id),
  owner_id uuid references app_user(id),
  reviewer_id uuid references app_user(id),
  title text not null,
  priority int not null default 100,
  lifecycle campaign_lifecycle not null default 'draft',
  control campaign_control not null default 'active',
  current_scope_version_id uuid,
  closure_reason text,
  next_human_action text,
  maintenance_enabled boolean not null default false,
  parent_campaign_id uuid references ingestion_campaign(id),
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);
create index on ingestion_campaign (tenant_id, lifecycle, priority);
create trigger campaign_touch before update on ingestion_campaign for each row execute function touch_updated_at();

create table campaign_scope_version (
  id uuid primary key default gen_random_uuid(),
  campaign_id uuid not null references ingestion_campaign(id),
  version int not null,
  ailment_text text,
  codes jsonb not null default '[]'::jsonb,            -- [{code, descriptor, release_id, laterality, resolved}]
  condition_ids uuid[] not null default '{}',
  inclusion jsonb not null default '{}'::jsonb,
  exclusion jsonb not null default '{}'::jsonb,
  refinements jsonb not null default '{}'::jsonb,      -- procedure, severity, phases, segments, setting, equipment, language, goals
  supplied_source_urls text[] not null default '{}',
  desired_output text not null,
  source_policy text not null default 'allowlist_only' check (source_policy in ('allowlist_only','supplied_only','allowlist_and_supplied')),
  limits jsonb not null,                               -- {max_search_requests, max_sources, max_document_bytes, max_candidates, max_runtime_seconds, max_usd}
  acceptance_criteria jsonb not null default '[]'::jsonb,
  media_policy text not null default 'reference_only' check (media_policy in ('none','reference_only','small_graphics')),
  authorized_by uuid references app_user(id),
  authorized_at timestamptz,
  created_at timestamptz not null default now(),
  unique (campaign_id, version)
);
alter table ingestion_campaign add constraint campaign_scope_fk foreign key (current_scope_version_id) references campaign_scope_version(id);

create table campaign_run (
  id uuid primary key default gen_random_uuid(),
  campaign_id uuid not null references ingestion_campaign(id),
  scope_version_id uuid not null references campaign_scope_version(id),
  run_number int not null,
  state run_state not null default 'pending',
  started_at timestamptz, finished_at timestamptz, paused_at timestamptz,
  heartbeat_at timestamptz,
  active_seconds int not null default 0,
  discovery_closed_at timestamptz,
  stop_reason text,
  progress jsonb not null default '{}'::jsonb,         -- reconciled from campaign_item/ingestion_job aggregates
  budget jsonb not null default '{"reserved_usd":0,"spent_usd":0,"search_requests":0,"sources":0,"candidates":0,"document_bytes":0}'::jsonb,
  revision int not null default 1,                      -- optimistic concurrency for version-checked actions
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  unique (campaign_id, run_number)
);
create trigger run_touch before update on campaign_run for each row execute function touch_updated_at();

create table campaign_item (
  id uuid primary key default gen_random_uuid(),
  campaign_id uuid not null references ingestion_campaign(id),
  run_id uuid references campaign_run(id),
  item_table text not null,                            -- source_version | ingestion_job | exercise_variant_version | clinical_use_version | evidence_claim
  item_id uuid not null,
  disposition text not null check (disposition in ('new','reused','rejected','duplicate','failed','pending')),
  detail jsonb not null default '{}'::jsonb,
  created_at timestamptz not null default now(),
  unique (campaign_id, item_table, item_id)
);
create index on campaign_item (run_id, disposition);

create table campaign_coverage_check (
  id uuid primary key default gen_random_uuid(),
  campaign_id uuid not null references ingestion_campaign(id),
  run_id uuid references campaign_run(id),
  criterion text not null,
  state text not null default 'unknown' check (state in ('unknown','unmet','met','not_applicable')),
  evidence jsonb not null default '{}'::jsonb,
  reviewer_id uuid references app_user(id),
  updated_at timestamptz not null default now(),
  unique (campaign_id, criterion)
);

create table campaign_event (
  id bigserial primary key,
  campaign_id uuid not null references ingestion_campaign(id),
  run_id uuid references campaign_run(id),
  event text not null,
  actor_id uuid,
  detail jsonb not null default '{}'::jsonb,
  created_at timestamptz not null default now()
);
create index on campaign_event (campaign_id, id);

create table budget_ledger (
  id bigserial primary key,
  run_id uuid not null references campaign_run(id),
  job_id uuid references ingestion_job(id),
  kind text not null check (kind in ('reserve','settle','release')),
  usd numeric(10,4) not null,
  search_requests int not null default 0,
  document_bytes bigint not null default 0,
  created_at timestamptz not null default now()
);

alter table ingestion_job add constraint job_campaign_fk foreign key (campaign_id) references ingestion_campaign(id);
alter table ingestion_job add constraint job_run_fk foreign key (campaign_run_id) references campaign_run(id);
alter table review_event add constraint review_campaign_fk foreign key (campaign_id) references ingestion_campaign(id);
alter table audit_event add constraint audit_campaign_fk foreign key (campaign_id) references ingestion_campaign(id);

-- Atomic budget reservation (§21E): reject if it cannot fit.
create or replace function reserve_budget(p_run_id uuid, p_job_id uuid, p_usd numeric, p_requests int, p_bytes bigint)
returns boolean language plpgsql as $$
declare
  lim jsonb; b jsonb;
begin
  select s.limits, r.budget into lim, b
    from campaign_run r join campaign_scope_version s on s.id = r.scope_version_id
   where r.id = p_run_id for update;
  if lim is null then return false; end if;
  if (b->>'reserved_usd')::numeric + (b->>'spent_usd')::numeric + p_usd > (lim->>'max_usd')::numeric then return false; end if;
  if (b->>'search_requests')::int + p_requests > (lim->>'max_search_requests')::int then return false; end if;
  if (b->>'document_bytes')::bigint + p_bytes > (lim->>'max_document_bytes')::bigint then return false; end if;
  update campaign_run set budget = b
      || jsonb_build_object('reserved_usd', (b->>'reserved_usd')::numeric + p_usd,
                            'search_requests', (b->>'search_requests')::int + p_requests,
                            'document_bytes', (b->>'document_bytes')::bigint + p_bytes)
   where id = p_run_id;
  insert into budget_ledger (run_id, job_id, kind, usd, search_requests, document_bytes)
    values (p_run_id, p_job_id, 'reserve', p_usd, p_requests, p_bytes);
  return true;
end $$;

create or replace function settle_budget(p_run_id uuid, p_job_id uuid, p_reserved numeric, p_actual numeric)
returns void language plpgsql as $$
declare b jsonb;
begin
  select budget into b from campaign_run where id = p_run_id for update;
  update campaign_run set budget = b
     || jsonb_build_object('reserved_usd', greatest((b->>'reserved_usd')::numeric - p_reserved, 0),
                           'spent_usd', (b->>'spent_usd')::numeric + p_actual)
   where id = p_run_id;
  insert into budget_ledger (run_id, job_id, kind, usd) values (p_run_id, p_job_id, 'settle', p_actual);
end $$;
