"""`source_admin` acts with every role the account holds, in the dev shim only.

Deliberate staging convenience: this deployment has one operator, the reviewer app defaults every new user to
`source_admin`, and there is nothing to protect yet — every content pack is still `unsigned_placeholder` and
nothing can reach `draft_ready`. Requiring a role switch before accepting a licence or approving an exercise was
friction nobody asked for, so an account that holds `source_admin` and every other role (which is exactly what
dev-login creates) is no longer narrowed down to `source_admin` alone just because that is what the UI sent.

What must NOT happen: an account that does not actually hold a role gaining it, and this reaching the Supabase
auth path at all. Both are asserted here, not just the happy path — this is exactly the kind of shortcut that is
easy to widen further than intended.
"""

from __future__ import annotations

import hashlib
import inspect
import uuid

import pytest
from moveai_db import J
from moveai_ingestion.source_policies import sync

from .conftest import As


@pytest.fixture()
def loaded(conn):
    sync(conn)
    return conn


def capture(conn, domain: str, text: str = "The terms of use for this site." * 20) -> str:
    sha = hashlib.sha256(text.encode()).hexdigest()
    conn.execute(
        "update source_policy set evidence=%s, evidence_state='captured' where domain=%s",
        (J({"sha256": sha, "fetched_at": "2026-09-12T00:00:00Z", "quoted_span": text[:300]}), domain),
    )
    return sha


@pytest.fixture()
def superuser(client, conn, seeded):
    """An account holding every role — what dev-login actually creates for a brand-new user. Deliberately distinct
    from the seeded `admin` fixture, which holds only source_admin, so the two never get confused."""
    row = conn.execute(
        "insert into app_user(tenant_id, email, display_name, roles) "
        "values (%s,%s,%s,(select array_agg(unnest) from unnest(enum_range(null::user_role)))) returning id",
        (seeded["tenant_id"], f"super-{uuid.uuid4().hex[:6]}@local.invalid", "super"),
    ).fetchone()
    return As(client, {**seeded, "super": row["id"]}, "super", role="source_admin")


@pytest.fixture()
def narrow_admin(client, conn, seeded):
    """Holds only source_admin — the ordinary case, unaffected by the widening because there is nothing to widen to."""
    row = conn.execute(
        "insert into app_user(tenant_id, email, display_name, roles) values (%s,%s,%s,'{source_admin}') returning id",
        (seeded["tenant_id"], f"onlyadmin-{uuid.uuid4().hex[:6]}@local.invalid", "onlyadmin"),
    ).fetchone()
    return As(client, {**seeded, "onlyadmin": row["id"]}, "onlyadmin", role="source_admin")


def test_source_admin_can_accept_licence_terms_without_switching_roles(loaded, superuser):
    """The exact friction Andy hit: signed in as source_admin, needed rights_reviewer to accept a publisher."""
    capture(loaded, "medlineplus.gov")
    r = superuser.post("/source-policies/medlineplus.gov/decision", {"decision": "sign", "note": "public domain"})
    assert r.status_code == 200, r.text
    assert r.json()["effective"] is True


def test_an_account_holding_only_source_admin_is_unaffected(loaded, narrow_admin):
    """Widening carries roles the account already holds; it invents none. An account that genuinely holds only
    source_admin still cannot accept licence terms, exactly as before."""
    capture(loaded, "medlineplus.gov")
    r = narrow_admin.post("/source-policies/medlineplus.gov/decision", {"decision": "sign"})
    assert r.status_code == 403


def test_selecting_a_narrower_role_still_narrows():
    """Explicitly picking a role other than source_admin must still narrow to just that role, so
    author-cannot-approve-their-own-work and similar checks can still be exercised deliberately."""
    from moveai_api.auth import _principal_from_row

    row = {
        "id": "x",
        "tenant_id": None,
        "email": "x",
        "display_name": "x",
        "roles": ["pt", "clinical_lead", "source_admin"],
        "auth_user_id": None,
    }
    p = _principal_from_row(row, "pt", None)
    assert p.roles == ["pt"]


def test_the_widening_only_applies_to_the_dev_shim():
    """This must never reach the Supabase path. A person's roles there are exactly what an administrator invited
    them with, and this convenience exists only because there is nothing yet worth protecting in shim mode."""
    from moveai_api.auth import _shim, _supabase

    assert "source_admin" in inspect.getsource(_shim)
    assert "source_admin" not in inspect.getsource(_supabase)
