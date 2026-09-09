-- 0001 foundation: extensions, enums, tenants, migration ledger, shared trigger functions.
-- Spec refs: §5 (states), §6 (knowledge model), §11 (roles), §19, §20A, §21.
create extension if not exists pgcrypto;

create table if not exists schema_migration (
  name text primary key,
  applied_at timestamptz not null default now()
);

-- ---------- enums ----------
create type permission_state as enum ('allowed','denied','unknown');
create type approval_state as enum (
  'draft','unsigned_placeholder','pending_review','approved','published',
  'invalidated','rejected','withdrawn','superseded');
create type pipeline_state as enum (
  'discovered','access_checked','fetched','parsed','extracted','normalized','evidence_linked',
  'pending_review','approved','published',
  'rights_hold','parse_failed','extraction_failed','conflict_hold','rejected','superseded','withdrawn');
create type media_state as enum ('not_requested','reference_only','graphic_available','rights_hold','unavailable');
create type assistance_mode as enum ('passive','active_assisted','active','resisted');
create type dose_provenance as enum ('source_explicit','clinician_authored','not_applicable','unknown');
create type field_status as enum ('known','unknown','not_assessed','not_applicable');
create type applicability_relationship as enum (
  'studied_population','guideline_recommended_population','supported_modification','precaution',
  'contraindication','clinician_consensus','insufficient_evidence','conflicting_evidence');
create type applicability_action as enum ('inform_only','rank','request_assessment','adapt','monitor','exclude');
create type support_category as enum (
  'direct_guideline_or_research','indirect_support','local_clinical_consensus','conflicting_support','insufficient_support');
create type user_role as enum ('source_admin','rights_reviewer','pt','clinical_lead','auditor','integration');
create type job_state as enum ('queued','running','succeeded','failed','dead_letter','cancelled');
create type plan_status as enum ('needs_assessment','blocked_for_clinical_review','draft_ready');
create type plan_version_status as enum ('draft','approved','superseded','review_required','withdrawn');
create type campaign_lifecycle as enum ('draft','queued','running','needs_attention','pt_review','complete','closed_incomplete');
create type campaign_control as enum ('active','paused','cancelled');
create type run_state as enum ('pending','running','paused','cancelled','finished');
create type review_decision as enum ('accept','edit','split','merge','reject','request_clarification','approve','withdraw','rights_allow','rights_deny');
create type dependency_type as enum ('derived_from','supported_by','uses_variant','uses_media','uses_rule','uses_protocol','uses_release','uses_clinical_use');

-- ---------- tenants ----------
create table tenant (
  id uuid primary key default gen_random_uuid(),
  name text not null unique,
  created_at timestamptz not null default now()
);

create table app_user (
  id uuid primary key default gen_random_uuid(),
  tenant_id uuid references tenant(id),
  email text not null unique,
  display_name text not null,
  roles user_role[] not null default '{}',
  created_at timestamptz not null default now()
);

-- ---------- shared trigger functions ----------
-- Immutable after approval (ADR-0004): once approval_state is approved/published, the only permitted
-- changes are the terminal transitions (withdrawn/superseded/invalidated) and their bookkeeping columns.
create or replace function enforce_version_immutability() returns trigger language plpgsql as $$
declare
  frozen_old jsonb; frozen_new jsonb;
begin
  if old.approval_state in ('approved','published') then
    if new.approval_state not in (old.approval_state, 'withdrawn', 'superseded', 'invalidated') then
      raise exception 'immutable_version: % % cannot transition from % to %', tg_table_name, old.id, old.approval_state, new.approval_state
        using errcode = 'P0001';
    end if;
    frozen_old := to_jsonb(old) - 'approval_state' - 'withdrawn_at' - 'withdrawal_reason' - 'superseded_by_id' - 'updated_at' - 'invalidated_at';
    frozen_new := to_jsonb(new) - 'approval_state' - 'withdrawn_at' - 'withdrawal_reason' - 'superseded_by_id' - 'updated_at' - 'invalidated_at';
    if frozen_old <> frozen_new then
      raise exception 'immutable_version: % % is % and cannot be edited; create a new version', tg_table_name, old.id, old.approval_state
        using errcode = 'P0001';
    end if;
  end if;
  new.updated_at := now();
  return new;
end $$;

create or replace function forbid_row_change() returns trigger language plpgsql as $$
begin
  raise exception 'append_only: % rows cannot be % ', tg_table_name, tg_op using errcode = 'P0001';
end $$;

create or replace function touch_updated_at() returns trigger language plpgsql as $$
begin new.updated_at := now(); return new; end $$;

-- Tenant context for RLS. NULL/empty means "no tenant" (catalog-only access).
create or replace function current_tenant() returns uuid language sql stable as $$
  select nullif(current_setting('app.tenant_id', true), '')::uuid
$$;
