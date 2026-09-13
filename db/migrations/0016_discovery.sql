-- 0016 automatic source discovery (spec §21, review milestone M7).
--
-- A campaign used to read only the URLs an operator typed plus sources already linked to its condition, so a
-- campaign for a brand-new condition had nothing to read. Discovery gives it somewhere to look: the site indexes
-- (robots.txt → sitemaps) of every publisher on the curated allowlist, and optionally a web search API.
--
-- What discovery does NOT change: whether a page may be *read*. Indexing a publisher's sitemap tells us which
-- pages exist; fetching any of them still requires that publisher's terms to be captured and signed
-- (source_policy.effective). Pages found on an unsigned publisher are parked as pending campaign items with the
-- reason, never fetched. Pages found on a domain with no policy at all are parked the same way.

-- The cached index of a publisher's site, so that repeated campaigns do not re-download the same sitemaps.
create table publisher_index (
  domain text primary key references source_policy(domain),
  fetched_at timestamptz,
  state text not null default 'unfetched' check (state in ('unfetched','fetched','unreachable','no_sitemap')),
  sitemap_urls jsonb not null default '[]'::jsonb,   -- the sitemap files that were read, in order
  robots_disallow jsonb not null default '[]'::jsonb,-- path prefixes robots.txt disallows for us; candidates under them are dropped
  url_count int not null default 0,
  bytes_read bigint not null default 0,
  error text,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);
create trigger publisher_index_touch before update on publisher_index for each row execute function touch_updated_at();

comment on table publisher_index is
  'Per-publisher cache of the site index (robots.txt and sitemaps). Knowing a page exists is not permission to read it: fetching still requires source_policy.effective.';

-- Every URL a publisher's sitemap lists. Bulk-loaded; scored per campaign against the condition''s names.
create table publisher_page (
  url text primary key,
  domain text not null references source_policy(domain),
  lastmod text,                                      -- as published in the sitemap, uninterpreted
  first_seen_at timestamptz not null default now(),
  last_seen_at timestamptz not null default now()
);
create index publisher_page_domain_idx on publisher_page (domain);

-- How a campaign item came to be: typed by the operator, already linked to the condition, found in a sitemap,
-- returned by a web search, or parked from an earlier run. Free text, recorded in campaign_item.detail.via; no
-- schema change to campaign_item is needed.

-- Supabase exposure boundary (see 0010/0012): every table in public carries RLS and at least one policy, or
-- verify_supabase.py fails the deployment.
alter table publisher_index enable row level security;
alter table publisher_page enable row level security;
do $$
begin
  if exists (select 1 from pg_roles where rolname = 'moveai_app') then
    create policy backend_all on publisher_index for all to moveai_app using (true) with check (true);
    create policy backend_all on publisher_page for all to moveai_app using (true) with check (true);
  else
    raise exception 'role moveai_app is missing; refusing to create a table with RLS on and no policy';
  end if;
end $$;
