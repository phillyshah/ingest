"""Grant a real person access to this deployment by binding their Supabase Auth identity to an app_user row.

Access is invitation-only (spec §22C). `services/api/moveai_api/auth.py` verifies a Supabase Auth JWT and then
looks the subject up in `app_user.auth_user_id`; with no matching row it returns 403 `not_invited`. A Supabase
Auth account on its own is therefore not authorization to use this service — this script is what turns one into
access, and it is the only thing that does.

The person must already exist in Supabase Auth (dashboard -> Authentication -> Users -> Add user -> Send invite).
Their auth id is read from `auth.users` over the same connection that runs migrations, so no service-role key and
no second secret are needed. Pass --auth-user-id to supply it directly, which is also how this runs against a
plain PostgreSQL database that has no `auth` schema.

Roles are validated against the `user_role` enum read from the database, never a list hardcoded here. An existing
user's roles are never changed implicitly: a differing set is refused unless --replace-roles says so, so a typo
cannot quietly hand someone clinical_lead.

    uv run python scripts/invite.py --email pt@clinic.example --roles pt
    uv run python scripts/invite.py --email lead@clinic.example --roles clinical_lead,source_admin --replace-roles
"""

from __future__ import annotations

import argparse
import sys
from typing import Any

import psycopg
from moveai_db import connect
from psycopg.rows import dict_row


class InviteError(RuntimeError):
    """A refusal the operator needs to read and act on, not a stack trace."""


def available_roles(conn: psycopg.Connection) -> list[str]:
    return [r["v"] for r in conn.execute("select unnest(enum_range(null::user_role))::text as v").fetchall()]


def resolve_tenant(conn: psycopg.Connection, name: str | None) -> str:
    """Name the tenant explicitly, or let a single-tenant deployment speak for itself."""
    if name:
        row = conn.execute("select id from tenant where name=%s", (name,)).fetchone()
        if not row:
            known = [r["name"] for r in conn.execute("select name from tenant order by name").fetchall()]
            raise InviteError(f"no tenant named {name!r}. Tenants present: {', '.join(known) or 'none — run the seed first'}")
        return str(row["id"])
    rows = conn.execute("select id, name from tenant order by name").fetchall()
    if len(rows) == 1:
        return str(rows[0]["id"])
    if not rows:
        raise InviteError("this database has no tenant yet. Run the Migrate Supabase workflow with the seed box ticked first.")
    raise InviteError(f"several tenants exist ({', '.join(r['name'] for r in rows)}); name one with --tenant")


def lookup_auth_user(conn: psycopg.Connection, email: str) -> str:
    """Find the Supabase Auth user id for an email address.

    Read-only against `auth.users`, which the migration role owns on a Supabase project. Both failure modes get a
    message that says what to do next, because the operator running this is not expected to debug it.
    """
    try:
        row = conn.execute("select id from auth.users where lower(email)=lower(%s)", (email,)).fetchone()
    except psycopg.errors.InsufficientPrivilege as e:
        raise InviteError(f"not allowed to read auth.users: {e}. Pass --auth-user-id instead.") from e
    except psycopg.errors.UndefinedTable as e:
        raise InviteError(
            "this database has no auth.users table, so it is not a Supabase project. Pass --auth-user-id to bind an identity by hand."
        ) from e
    if not row:
        raise InviteError(
            f"{email} has no Supabase Auth account yet. Create one first: Supabase dashboard -> Authentication -> "
            "Users -> Add user -> Send invite, then run this again."
        )
    return str(row["id"])


def invite(
    conn: psycopg.Connection,
    *,
    email: str,
    roles: list[str],
    display_name: str | None = None,
    tenant: str | None = None,
    auth_user_id: str | None = None,
    replace_roles: bool = False,
) -> dict[str, Any]:
    """Create or update one app_user and bind it to a Supabase Auth identity. Idempotent; does not commit."""
    email = email.strip()
    if not email or "@" not in email:
        raise InviteError(f"{email!r} is not an email address")

    roles = [r.strip() for r in roles if r.strip()]
    if not roles:
        raise InviteError("give at least one role with --roles; a user with no roles is refused at sign-in anyway")
    allowed = available_roles(conn)
    unknown = [r for r in roles if r not in allowed]
    if unknown:
        raise InviteError(f"unknown role(s): {', '.join(unknown)}. Valid roles: {', '.join(allowed)}")
    roles = sorted(set(roles))

    subject = auth_user_id or lookup_auth_user(conn, email)

    # The unique index on auth_user_id means one identity cannot be bound to two application users. Catch it here
    # so the operator gets a sentence rather than a constraint violation.
    clash = conn.execute("select email from app_user where auth_user_id=%s and lower(email)<>lower(%s)", (subject, email)).fetchone()
    if clash:
        raise InviteError(f"that Supabase Auth identity is already bound to {clash['email']}. Unbind it first.")

    existing = conn.execute(
        "select id, roles::text[] as roles, auth_user_id, tenant_id from app_user where lower(email)=lower(%s)", (email,)
    ).fetchone()
    tenant_id = resolve_tenant(conn, tenant) if (tenant or not existing) else str(existing["tenant_id"])

    if existing:
        current = sorted(existing["roles"] or [])
        if current != roles and not replace_roles:
            raise InviteError(
                f"{email} already exists with roles [{', '.join(current) or 'none'}] and you asked for "
                f"[{', '.join(roles)}]. Re-run with --replace-roles if that change is intended."
            )
        row = conn.execute(
            "update app_user set roles=%s, auth_user_id=%s, tenant_id=%s where id=%s returning id, email, roles::text[] as roles, auth_user_id",
            (roles, subject, tenant_id, existing["id"]),
        ).fetchone()
        action = "updated"
    else:
        row = conn.execute(
            "insert into app_user(tenant_id, email, display_name, roles, auth_user_id) values (%s,%s,%s,%s,%s) "
            "returning id, email, roles::text[] as roles, auth_user_id",
            (tenant_id, email, display_name or email.split("@")[0], roles, subject),
        ).fetchone()
        action = "created"
    return {"action": action, "tenant_id": tenant_id, **row}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Give a Supabase Auth user access to this deployment.")
    ap.add_argument("--email", required=True, help="the person's email, as it appears in Supabase Auth")
    ap.add_argument("--roles", required=True, help="comma-separated, e.g. pt or clinical_lead,source_admin")
    ap.add_argument("--display-name", default=None, help="defaults to the part of the email before the @")
    ap.add_argument("--tenant", default=None, help="tenant name; only needed when more than one exists")
    ap.add_argument("--auth-user-id", default=None, help="bind this Supabase Auth id instead of looking the email up")
    ap.add_argument("--replace-roles", action="store_true", help="allow changing an existing user's roles")
    args = ap.parse_args(argv)

    with connect() as conn:
        conn.row_factory = dict_row
        try:
            out = invite(
                conn,
                email=args.email,
                roles=args.roles.split(","),
                display_name=args.display_name,
                tenant=args.tenant,
                auth_user_id=args.auth_user_id,
                replace_roles=args.replace_roles,
            )
        except InviteError as e:
            conn.rollback()
            print(f"refused: {e}", file=sys.stderr)
            return 2
        conn.commit()
    print(f"{out['action']} {out['email']} roles=[{', '.join(out['roles'])}] app_user={out['id']} auth_user={out['auth_user_id']}")
    print("They can now sign in at the deployment with the password they set from the Supabase invite email.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
