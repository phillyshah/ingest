-- 0013 let an operator remove a campaign from the board.
--
-- Deliberately a soft delete. `audit_event`, `review_event` and `ingestion_job` all carry a foreign key to
-- ingestion_campaign, and audit_event is append-only by trigger: a hard delete would either violate the foreign
-- key or require erasing audit rows, and "who asked for what content to be ingested" is exactly the sort of thing
-- an audit trail exists to keep. The row stays; it stops being listed.
alter table ingestion_campaign add column if not exists deleted_at timestamptz;
alter table ingestion_campaign add column if not exists deleted_by uuid references app_user(id);

comment on column ingestion_campaign.deleted_at is
  'Set when an operator removes the campaign from the board. The row and its audit trail are retained; listings filter it out.';

-- Listings filter on (tenant_id, deleted_at) constantly; the existing index does not cover it.
create index if not exists ingestion_campaign_visible_idx
  on ingestion_campaign (tenant_id, lifecycle, priority)
  where deleted_at is null;
