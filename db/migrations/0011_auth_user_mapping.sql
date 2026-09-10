-- 0011 map a Supabase Auth user to an application user (spec §22C, invitation-only access).
-- Nullable: development uses the header shim and has no auth users. Unique: one auth identity, one app user.
alter table app_user add column if not exists auth_user_id uuid;
create unique index if not exists app_user_auth_user_id_key on app_user (auth_user_id) where auth_user_id is not null;
comment on column app_user.auth_user_id is
  'Supabase Auth subject (auth.users.id). No self-enrollment: a verified token whose subject has no row here is refused.';
