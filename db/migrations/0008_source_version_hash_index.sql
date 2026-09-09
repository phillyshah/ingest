-- 0008: a re-fetched, unchanged document records the same hash on a new source_version that is immediately
-- marked superseded (spec §5.3). Uniqueness on (source_id, hash) blocked that; use a plain index instead.
alter table source_version drop constraint if exists source_version_source_id_content_sha256_key;
create index if not exists source_version_hash_idx on source_version (source_id, content_sha256);
