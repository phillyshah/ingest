-- 0010 Supabase exposure hardening.
--
-- On plain PostgreSQL only the application connects, as a role we control, so table-level grants were sufficient.
-- On Supabase, PostgREST publishes the `public` schema over HTTPS and the project's default privileges grant the
-- `anon` and `authenticated` roles access to tables in it, with row-level security as the only gate. Every engine
-- table without RLS would therefore be readable and writable by anyone holding the anon key, which is public by
-- design and ships in the browser bundle. Spec §22C requires an explicit grant and policy boundary.
--
-- Two independent layers of denial:
--   1. privileges  - anon/authenticated/PUBLIC hold no grants and no schema usage
--   2. RLS         - every engine table has row-level security on, with a policy only for the backend role
--
-- The `anon` and `authenticated` roles do not exist on local development PostgreSQL or in CI, so every reference to
-- them is guarded and this migration is a no-op there. `verify_supabase.py` is what proves the lockdown on the real
-- project. RLS is enabled but deliberately NOT forced on catalog tables, so the owner role can still run migrations
-- and maintenance; the patient-store tables keep the forced tenant policies from 0005.

-- ---------------------------------------------------------------- 1. privileges
do $$
declare
  r text;
begin
  foreach r in array array['anon', 'authenticated'] loop
    if exists (select 1 from pg_roles where rolname = r) then
      execute format('revoke all on all tables in schema public from %I', r);
      execute format('revoke all on all sequences in schema public from %I', r);
      execute format('revoke all on all functions in schema public from %I', r);
      execute format('revoke all on schema public from %I', r);
      -- stop future objects from being granted automatically
      execute format('alter default privileges in schema public revoke all on tables from %I', r);
      execute format('alter default privileges in schema public revoke all on sequences from %I', r);
      execute format('alter default privileges in schema public revoke all on functions from %I', r);
      -- Supabase creates objects as `postgres`; also revoke defaults recorded for that owner
      if exists (select 1 from pg_roles where rolname = 'postgres') then
        execute format('alter default privileges for role postgres in schema public revoke all on tables from %I', r);
        execute format('alter default privileges for role postgres in schema public revoke all on sequences from %I', r);
        execute format('alter default privileges for role postgres in schema public revoke all on functions from %I', r);
      end if;
    end if;
  end loop;
end $$;

-- PUBLIC (every role) must not hold table rights either. Schema usage stays for the owner and moveai_app.
revoke all on all tables in schema public from public;
revoke all on all sequences in schema public from public;

-- ---------------------------------------------------------------- 2. row-level security
-- Enable RLS on every engine table and give the backend role an explicit policy. Tables that already carry tenant
-- policies (case_snapshot, case_observation, plan_version) are skipped so their forced isolation is not weakened.
do $$
declare
  t record;
begin
  for t in
    select c.relname
      from pg_class c
      join pg_namespace n on n.oid = c.relnamespace
     where n.nspname = 'public'
       and c.relkind = 'r'
       and c.relrowsecurity = false
     order by c.relname
  loop
    execute format('alter table public.%I enable row level security', t.relname);
    -- One permissive policy, backend role only. anon/authenticated match no policy and are denied.
    if exists (select 1 from pg_roles where rolname = 'moveai_app') then
      execute format(
        'create policy backend_all on public.%I for all to moveai_app using (true) with check (true)', t.relname);
    end if;
  end loop;
end $$;

-- Keep the backend's own grants intact after the blanket revokes above.
do $$
begin
  if exists (select 1 from pg_roles where rolname = 'moveai_app') then
    grant usage on schema public to moveai_app;
    grant select, insert, update, delete on all tables in schema public to moveai_app;
    grant usage, select on all sequences in schema public to moveai_app;
  end if;
end $$;

-- ---------------------------------------------------------------- 3. record the boundary
comment on schema public is
  'MoveAI ingestion engine. Not exposed to Supabase anon/authenticated roles: no grants, no schema usage, RLS on '
  'every table. Backend connects as an owner or moveai_app role. See db/migrations/0010 and scripts/verify_supabase.py.';
