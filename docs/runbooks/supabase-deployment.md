# Runbook: deploying the schema to a Supabase project

## Before you start

- Confirm the exact project. Spec §22C: the owner names the provider, not a project. Migrating the wrong project
  is not reversible with an undo button.
- The project should be dedicated to this engine. If it is shared, every revoke and policy must be scoped to this
  engine's tables and the other application's objects verified afterwards.
- Have the connection string ready: Supabase dashboard → Project Settings → Database → Connection string →
  **Session pooler**. Prefer the pooler: the direct `db.<ref>.supabase.co` host is IPv6-only from many networks.

## Apply

```bash
export DATABASE_URL='postgresql://postgres.<ref>:<password>@aws-0-<region>.pooler.supabase.com:5432/postgres'
uv run python scripts/migrate.py            # prints the target host first; never pass --reset here
uv run python scripts/verify_supabase.py    # must print PASSED
```

`migrate.py` refuses `--reset` unless `ALLOW_DESTRUCTIVE_RESET=1`, and refuses it outright against a managed host.
`--reset` drops the `public` schema. There is no reason to run it against a real project.

`verify_supabase.py` is the acceptance check. It must report no `anon` or `authenticated` privilege on any engine
table, row-level security on all of them, and at least one policy each. If it fails, do not deploy the API.

## Seed

```bash
uv run python scripts/seed.py               # terminology + the three unsigned clinical packs
```

`demo_synthetic` is excluded by default. It is approved and can produce `draft_ready` plans, so it must never
reach a real environment unless you are deliberately demonstrating the approval path; pass `--with-demo-pack` to
include it, or `--packs a,b` to choose exactly.

## Switch on real authentication

```
AUTH_MODE=supabase
SUPABASE_URL=https://<ref>.supabase.co
# SUPABASE_JWT_SECRET=...   only for legacy shared-secret projects; omit to use the project's JWKS
```

Access is invitation-only. Signing up in Supabase Auth grants nothing. An administrator links an auth identity to
an application user:

```sql
update app_user set auth_user_id = '<auth.users.id>' where email = 'reviewer@example.com';
```

A verified token whose subject has no `app_user` row gets 403 `not_invited`. Setting `MOVEAI_ENV=production` makes
the API refuse to start requests under the development header shim.

## Rotate the password

Rotate the database password after any migration run where the connection string passed through a shared context
such as a chat transcript, a CI log or a shared terminal. Dashboard → Project Settings → Database → Reset database
password, then update `DATABASE_URL` wherever it is configured and restart the API and worker.

## Rollback

The migrations are additive, so rolling the application back to a previous image is safe. Do not drop the new
objects. To undo the hardening specifically, restore the grants Supabase creates by default and disable RLS on the
affected tables. Only do that on a scratch project; on a real one it re-exposes every table to the anon key.

## Backups

Verify the project's backup and point-in-time-recovery capability before relying on it. Do not assume the plan
includes it. Test a restore into a scratch project and confirm `catalog_release` manifest hashes still match and
`plan_version` signatures still verify under the same `PLAN_SIGNING_SECRET`.
