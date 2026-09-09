-- 0006 review events, audit log, ingestion jobs, outbox. Spec §5, §6, §10.
create table review_event (
  id uuid primary key default gen_random_uuid(),
  tenant_id uuid references tenant(id),
  entity_table text not null,
  version_id uuid not null,
  reviewer_id uuid not null references app_user(id),
  reviewer_role user_role not null,
  decision review_decision not null,
  changes jsonb,
  reason text,
  time_spent_seconds int,
  campaign_id uuid,                                    -- FK in 0007
  created_at timestamptz not null default now()
);
create index on review_event (entity_table, version_id);

create table audit_event (
  id uuid primary key default gen_random_uuid(),
  tenant_id uuid,
  actor_id uuid,
  actor_kind text not null check (actor_kind in ('user','service','worker')),
  action text not null,
  request_id text,
  entity_table text,
  entity_id uuid,
  campaign_id uuid,
  detail jsonb not null default '{}'::jsonb,
  created_at timestamptz not null default now()
);
create index on audit_event (created_at desc);
create trigger audit_append_only before update or delete on audit_event for each row execute function forbid_row_change();

create table ingestion_job (
  id uuid primary key default gen_random_uuid(),
  tenant_id uuid references tenant(id),
  source_version_id uuid references source_version(id),
  stage text not null,
  idempotency_key text not null unique,                -- (source_version_id, stage, parser_version, schema_version, prompt_version, model_version)
  state job_state not null default 'queued',
  priority int not null default 100,
  attempts int not null default 0,
  max_attempts int not null default 5,
  run_after timestamptz not null default now(),
  leased_until timestamptz,
  worker_id text,
  heartbeat_at timestamptz,
  checkpoint jsonb not null default '{}'::jsonb,
  payload jsonb not null default '{}'::jsonb,
  error_class text,
  last_error text,
  warnings jsonb not null default '[]'::jsonb,
  cost_usd numeric(10,4) not null default 0,
  duration_ms int,
  campaign_id uuid,
  campaign_run_id uuid,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  finished_at timestamptz
);
create index on ingestion_job (state, priority, run_after) where state in ('queued','running');
create index on ingestion_job (campaign_run_id);
create trigger job_touch before update on ingestion_job for each row execute function touch_updated_at();

create table job_event (
  id bigserial primary key,
  job_id uuid not null references ingestion_job(id),
  event text not null,
  detail jsonb not null default '{}'::jsonb,
  created_at timestamptz not null default now()
);
create index on job_event (job_id, id);

-- transactional outbox for integration events (§10)
create table outbox_event (
  id uuid primary key default gen_random_uuid(),
  event_type text not null check (event_type in ('catalog.published','content.withdrawn','rights.expired','plan.approved','plan.review_required')),
  schema_version int not null default 1,
  tenant_id uuid,
  object_table text,
  object_id uuid,
  object_version int,
  payload jsonb not null default '{}'::jsonb,          -- minimized; no patient data
  created_at timestamptz not null default now(),
  delivered_at timestamptz,
  attempts int not null default 0,
  dead_lettered boolean not null default false
);
create index on outbox_event (created_at) where delivered_at is null;

-- catalog change feed for GET /changes (§10)
create table catalog_change (
  cursor bigserial primary key,
  change_type text not null check (change_type in ('published','withdrawn','superseded','rights_expired')),
  entity_table text not null,
  version_id uuid not null,
  release_id uuid references catalog_release(id),
  created_at timestamptz not null default now()
);

-- idempotency for mutating client requests
create table idempotency_key (
  key text not null,
  tenant_id uuid,
  user_id uuid,
  request_hash text not null,
  status_code int not null,
  response jsonb not null,
  created_at timestamptz not null default now(),
  primary key (key, tenant_id, user_id)
);
