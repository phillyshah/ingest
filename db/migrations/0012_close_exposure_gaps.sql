-- 0012 close the two gaps that verify_supabase.py caught on the real Supabase project.
--
-- Migration 0010 was written and tested against a local database whose `public` schema had been dropped and
-- recreated. A recreated schema carries no grant to the PUBLIC pseudo-role, so two defects were invisible locally:
--
--   Gap 1  PostgreSQL grants USAGE on the original `public` schema to PUBLIC, which every role inherits, including
--          anon and authenticated. 0010 revoked table, sequence and function privileges from PUBLIC and revoked
--          schema usage from those two roles by name, but never revoked schema usage from PUBLIC itself.
--
--   Gap 2  0010 created the backend policy only `if exists (... rolname = 'moveai_app')`. On the real project that
--          guard did not fire while the surrounding `enable row level security` did, leaving 41 tables with RLS on
--          and no policy. The guard is the defect: a conditional that silently skips a security control is wrong
--          whatever the reason it fires. This migration never skips; it raises.
--
-- Fail-closed, not fail-open: a table with RLS enabled and no policy denies everyone but the owner, so nothing was
-- exposed by gap 2. Gap 1 alone grants no table access either, since reading a table needs a table privilege too.

-- ---------------------------------------------------------------- gap 1: the PUBLIC pseudo-role
-- postgres keeps its own explicit grant, moveai_app is re-granted below, and Supabase grants service_role
-- explicitly, so this removes inherited access without disturbing anything that holds a real grant.
revoke usage on schema public from public;

do $$
declare
  r text;
begin
  foreach r in array array['anon', 'authenticated'] loop
    if exists (select 1 from pg_roles where rolname = r) then
      execute format('revoke all on schema public from %I', r);
      execute format('revoke all on all tables in schema public from %I', r);
      execute format('revoke all on all sequences in schema public from %I', r);
      execute format('revoke all on all functions in schema public from %I', r);
      execute format('alter default privileges in schema public revoke all on tables from %I', r);
      execute format('alter default privileges in schema public revoke all on sequences from %I', r);
      execute format('alter default privileges in schema public revoke all on functions from %I', r);
      if exists (select 1 from pg_roles where rolname = 'postgres') then
        execute format('alter default privileges for role postgres in schema public revoke all on tables from %I', r);
        execute format('alter default privileges for role postgres in schema public revoke all on sequences from %I', r);
        execute format('alter default privileges for role postgres in schema public revoke all on functions from %I', r);
      end if;
    end if;
  end loop;
end $$;

-- ---------------------------------------------------------------- gap 2: a policy on every table, no exceptions
do $$
declare
  t record;
begin
  if not exists (select 1 from pg_roles where rolname = 'moveai_app') then
    create role moveai_app nologin;
  end if;
  if not exists (select 1 from pg_roles where rolname = 'moveai_app') then
    raise exception 'could not create role moveai_app; refusing to leave tables with row-level security and no policy';
  end if;

  for t in
    select c.relname
      from pg_class c
      join pg_namespace n on n.oid = c.relnamespace
     where n.nspname = 'public'
       and c.relkind = 'r'
       and not exists (select 1 from pg_policies p where p.schemaname = 'public' and p.tablename = c.relname)
     order by c.relname
  loop
    execute format('alter table public.%I enable row level security', t.relname);
    execute format('create policy backend_all on public.%I for all to moveai_app using (true) with check (true)', t.relname);
  end loop;
end $$;

grant usage on schema public to moveai_app;
grant select, insert, update, delete on all tables in schema public to moveai_app;
grant usage, select on all sequences in schema public to moveai_app;

-- ---------------------------------------------------------------- self-verification
-- The migration runs inside a transaction, so raising here rolls the whole thing back rather than leaving a
-- half-configured schema. This is the assertion 0010 should have carried.
do $$
declare
  missing   text[];
  leaked    text[];
  role_name text;
begin
  select array_agg(c.relname order by c.relname) into missing
    from pg_class c
    join pg_namespace n on n.oid = c.relnamespace
   where n.nspname = 'public' and c.relkind = 'r' and c.relrowsecurity
     and not exists (select 1 from pg_policies p where p.schemaname = 'public' and p.tablename = c.relname);
  if missing is not null then
    raise exception 'row-level security is on with no policy for: %', array_to_string(missing, ', ');
  end if;

  -- An explicit loop, not a WHERE clause: SQL does not short-circuit AND, so has_schema_privilege() would be
  -- evaluated for roles that do not exist and raise "role does not exist" on a local database.
  leaked := '{}';
  foreach role_name in array array['anon', 'authenticated'] loop
    if exists (select 1 from pg_roles where rolname = role_name) then
      if has_schema_privilege(role_name, 'public', 'usage') then
        leaked := leaked || role_name;
      end if;
    end if;
  end loop;
  if array_length(leaked, 1) is not null then
    raise exception 'these roles still hold usage on schema public: %', array_to_string(leaked, ', ');
  end if;
end $$;
