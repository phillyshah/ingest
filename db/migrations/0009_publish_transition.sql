-- 0009: catalog publication moves approved -> published (still frozen). Allow that one forward transition.
create or replace function enforce_version_immutability() returns trigger language plpgsql as $$
declare
  frozen_old jsonb; frozen_new jsonb;
begin
  if old.approval_state in ('approved','published') then
    if new.approval_state not in (old.approval_state, 'published', 'withdrawn', 'superseded', 'invalidated')
       or (old.approval_state = 'published' and new.approval_state = 'approved') then
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
