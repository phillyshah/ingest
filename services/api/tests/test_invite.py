"""scripts/invite.py is the only thing that turns a Supabase Auth account into access (spec §22C).

These tests cover the refusals as much as the happy path, because every refusal here is a case where a mistake
would otherwise hand a real person clinical authority they were not meant to have.
"""

from __future__ import annotations

import importlib.util
import uuid
from pathlib import Path

import pytest

from .test_auth import bearer, supabase_mode, token  # noqa: F401  (supabase_mode is a fixture)

_spec = importlib.util.spec_from_file_location("moveai_invite", Path(__file__).resolve().parents[3] / "scripts" / "invite.py")
invite_mod = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(invite_mod)
InviteError = invite_mod.InviteError


def email() -> str:
    return f"{uuid.uuid4().hex[:8]}@clinic.example"


def test_invites_a_new_user_and_binds_the_auth_identity(conn, seeded):
    auth_id, addr = str(uuid.uuid4()), email()
    out = invite_mod.invite(conn, email=addr, roles=["pt"], auth_user_id=auth_id)
    assert out["action"] == "created"
    assert out["roles"] == ["pt"]
    assert str(out["auth_user_id"]) == auth_id
    assert str(out["tenant_id"]) == str(seeded["tenant_id"])


def test_running_it_twice_changes_nothing(conn, seeded):
    auth_id, addr = str(uuid.uuid4()), email()
    first = invite_mod.invite(conn, email=addr, roles=["pt"], auth_user_id=auth_id)
    second = invite_mod.invite(conn, email=addr, roles=["pt"], auth_user_id=auth_id)
    assert second["action"] == "updated"
    assert second["id"] == first["id"]
    assert conn.execute("select count(*) as n from app_user where lower(email)=lower(%s)", (addr,)).fetchone()["n"] == 1


def test_an_unknown_role_is_refused(conn, seeded):
    with pytest.raises(InviteError, match="unknown role"):
        invite_mod.invite(conn, email=email(), roles=["superuser"], auth_user_id=str(uuid.uuid4()))


def test_no_roles_is_refused(conn, seeded):
    with pytest.raises(InviteError, match="at least one role"):
        invite_mod.invite(conn, email=email(), roles=[], auth_user_id=str(uuid.uuid4()))


def test_existing_roles_are_never_widened_silently(conn, seeded):
    """The typo guard: asking for a different role set on an existing user must stop, not escalate them."""
    auth_id, addr = str(uuid.uuid4()), email()
    invite_mod.invite(conn, email=addr, roles=["pt"], auth_user_id=auth_id)
    with pytest.raises(InviteError, match="replace-roles"):
        invite_mod.invite(conn, email=addr, roles=["clinical_lead"], auth_user_id=auth_id)
    assert conn.execute("select roles::text[] as r from app_user where lower(email)=lower(%s)", (addr,)).fetchone()["r"] == ["pt"]

    changed = invite_mod.invite(conn, email=addr, roles=["clinical_lead"], auth_user_id=auth_id, replace_roles=True)
    assert changed["roles"] == ["clinical_lead"]


def test_one_auth_identity_cannot_be_bound_to_two_users(conn, seeded):
    auth_id = str(uuid.uuid4())
    invite_mod.invite(conn, email=email(), roles=["pt"], auth_user_id=auth_id)
    with pytest.raises(InviteError, match="already bound"):
        invite_mod.invite(conn, email=email(), roles=["pt"], auth_user_id=auth_id)


def test_a_database_without_supabase_auth_says_so(conn, seeded):
    """Local PostgreSQL has no auth schema; the message must send the operator to --auth-user-id, not a traceback."""
    with pytest.raises(InviteError, match="not a Supabase project"):
        invite_mod.invite(conn, email=email(), roles=["pt"])


def test_an_unnamed_tenant_is_refused_when_several_exist(conn, seeded):
    conn.execute("insert into tenant(name) values (%s)", (f"other-{uuid.uuid4().hex[:6]}",))
    with pytest.raises(InviteError, match="--tenant"):
        invite_mod.invite(conn, email=email(), roles=["pt"], auth_user_id=str(uuid.uuid4()))


def test_the_invited_user_can_actually_sign_in(client, conn, seeded, supabase_mode):  # noqa: F811
    """The point of all of it: after inviting, a real Supabase token is accepted instead of 403 not_invited."""
    auth_id, addr = str(uuid.uuid4()), email()
    refused = client.get("/v1/me", headers=bearer(token(auth_id)))
    assert refused.status_code == 403 and refused.json()["code"] == "not_invited"

    invite_mod.invite(conn, email=addr, roles=["pt"], auth_user_id=auth_id)
    accepted = client.get("/v1/me", headers=bearer(token(auth_id)))
    assert accepted.status_code == 200 and accepted.json()["roles"] == ["pt"]
