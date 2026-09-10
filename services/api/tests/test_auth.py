"""Supabase Auth verification (spec §22C). Access is invitation-only: a valid token is not an authorization."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import jwt
import pytest

SECRET = "test-shared-secret-not-for-production"
PROJECT = "https://example-project.supabase.co"
ISSUER = f"{PROJECT}/auth/v1"


@pytest.fixture()
def supabase_mode(monkeypatch):
    monkeypatch.setenv("AUTH_MODE", "supabase")
    monkeypatch.setenv("SUPABASE_URL", PROJECT)
    monkeypatch.setenv("SUPABASE_JWT_SECRET", SECRET)
    monkeypatch.delenv("MOVEAI_ENV", raising=False)


def token(sub: str, *, secret: str = SECRET, aud: str = "authenticated", iss: str = ISSUER, ttl: int = 3600, **extra) -> str:
    now = datetime.now(UTC)
    claims = {"sub": sub, "aud": aud, "iss": iss, "iat": now, "exp": now + timedelta(seconds=ttl), **extra}
    return jwt.encode(claims, secret, algorithm="HS256")


def invite(conn, tenant_id, roles: str = "{pt}") -> tuple[str, str]:
    """Create an app_user linked to a Supabase auth subject, as an administrator invitation would."""
    auth_id = uuid.uuid4()
    row = conn.execute(
        "insert into app_user(tenant_id, email, display_name, roles, auth_user_id) values (%s,%s,'invited',%s,%s) returning id",
        (tenant_id, f"{auth_id}@example.test", roles, auth_id),
    ).fetchone()
    return str(auth_id), str(row["id"])


def bearer(t: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {t}"}


def test_valid_token_for_an_invited_user(client, conn, seeded, supabase_mode):
    auth_id, user_id = invite(conn, seeded["tenant_id"])
    r = client.get("/v1/me", headers=bearer(token(auth_id)))
    assert r.status_code == 200, r.text
    assert r.json()["user_id"] == user_id and r.json()["roles"] == ["pt"]


def test_valid_token_without_an_invitation_is_refused(client, conn, seeded, supabase_mode):
    r = client.get("/v1/me", headers=bearer(token(str(uuid.uuid4()))))
    assert r.status_code == 403 and r.json()["code"] == "not_invited"


def test_invited_user_with_no_roles_is_refused(client, conn, seeded, supabase_mode):
    auth_id, _ = invite(conn, seeded["tenant_id"], roles="{}")
    r = client.get("/v1/me", headers=bearer(token(auth_id)))
    assert r.status_code == 403 and r.json()["code"] == "no_roles"


def test_expired_token(client, conn, seeded, supabase_mode):
    auth_id, _ = invite(conn, seeded["tenant_id"])
    r = client.get("/v1/me", headers=bearer(token(auth_id, ttl=-60)))
    assert r.status_code == 401 and r.json()["code"] == "token_expired"


def test_wrong_signature(client, conn, seeded, supabase_mode):
    auth_id, _ = invite(conn, seeded["tenant_id"])
    r = client.get("/v1/me", headers=bearer(token(auth_id, secret="a-different-secret-of-sufficient-length-32b")))
    assert r.status_code == 401 and r.json()["code"] == "invalid_token"


def test_wrong_audience_and_issuer(client, conn, seeded, supabase_mode):
    auth_id, _ = invite(conn, seeded["tenant_id"])
    assert client.get("/v1/me", headers=bearer(token(auth_id, aud="anon"))).json()["code"] == "bad_audience"
    r = client.get("/v1/me", headers=bearer(token(auth_id, iss="https://attacker.example/auth/v1")))
    assert r.status_code == 401 and r.json()["code"] == "bad_issuer"


def test_unsigned_and_missing_tokens(client, conn, seeded, supabase_mode):
    assert client.get("/v1/me").status_code == 401
    assert client.get("/v1/me", headers={"Authorization": "Basic abc"}).json()["code"] == "unauthenticated"
    none_alg = jwt.encode({"sub": str(uuid.uuid4()), "aud": "authenticated", "iss": ISSUER}, key="", algorithm="none")
    assert client.get("/v1/me", headers=bearer(none_alg)).status_code == 401


def test_role_narrowing_and_tenant_mismatch(client, conn, seeded, supabase_mode):
    auth_id, _ = invite(conn, seeded["tenant_id"], roles="{pt,clinical_lead}")
    r = client.get("/v1/me", headers={**bearer(token(auth_id)), "X-Role": "clinical_lead"})
    assert r.json()["roles"] == ["clinical_lead"]
    r = client.get("/v1/me", headers={**bearer(token(auth_id)), "X-Role": "source_admin"})
    assert r.status_code == 403 and r.json()["code"] == "role_not_held"
    r = client.get("/v1/me", headers={**bearer(token(auth_id)), "X-Tenant-Id": str(uuid.uuid4())})
    assert r.status_code == 403 and r.json()["code"] == "tenant_mismatch"


def test_asymmetric_jwks_path(client, conn, seeded, monkeypatch):
    """Supabase signs with asymmetric keys by default; the shared secret is the legacy fallback."""
    from cryptography.hazmat.primitives.asymmetric import rsa

    monkeypatch.setenv("AUTH_MODE", "supabase")
    monkeypatch.setenv("SUPABASE_URL", PROJECT)
    monkeypatch.delenv("SUPABASE_JWT_SECRET", raising=False)
    monkeypatch.delenv("MOVEAI_ENV", raising=False)
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    auth_id, user_id = invite(conn, seeded["tenant_id"])
    now = datetime.now(UTC)
    signed = jwt.encode(
        {"sub": auth_id, "aud": "authenticated", "iss": ISSUER, "iat": now, "exp": now + timedelta(hours=1)}, key, algorithm="RS256"
    )

    class _Key:
        def __init__(self, k):
            self.key = k

    class _Client:
        def get_signing_key_from_jwt(self, _token):
            return _Key(key.public_key())

    monkeypatch.setattr("moveai_api.auth._jwks_client", lambda: _Client())
    r = client.get("/v1/me", headers=bearer(signed))
    assert r.status_code == 200 and r.json()["user_id"] == user_id
    # a token signed by a different key must fail even though the JWKS lookup succeeds
    other = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    bad = jwt.encode(
        {"sub": auth_id, "aud": "authenticated", "iss": ISSUER, "iat": now, "exp": now + timedelta(hours=1)}, other, algorithm="RS256"
    )
    assert client.get("/v1/me", headers=bearer(bad)).status_code == 401


def test_jwks_unavailable_never_lets_a_request_through(client, conn, seeded, monkeypatch):
    monkeypatch.setenv("AUTH_MODE", "supabase")
    monkeypatch.setenv("SUPABASE_URL", PROJECT)
    monkeypatch.delenv("SUPABASE_JWT_SECRET", raising=False)

    def boom():
        raise ConnectionError("network down")

    monkeypatch.setattr("moveai_api.auth._jwks_client", boom)
    r = client.get("/v1/me", headers=bearer(token(str(uuid.uuid4()))))
    assert r.status_code == 401 and r.json()["code"] == "verification_unavailable"


def test_shim_is_refused_in_production(client, conn, seeded, monkeypatch):
    monkeypatch.setenv("AUTH_MODE", "shim")
    monkeypatch.setenv("MOVEAI_ENV", "production")
    r = client.get("/v1/me", headers={"X-User-Id": str(seeded["pt"])})
    assert r.status_code == 500 and r.json()["code"] == "auth_misconfigured"


def test_shim_still_works_in_development(client, conn, seeded, monkeypatch):
    monkeypatch.setenv("AUTH_MODE", "shim")
    monkeypatch.setenv("MOVEAI_ENV", "test")
    r = client.get("/v1/me", headers={"X-User-Id": str(seeded["pt"])})
    assert r.status_code == 200 and r.json()["roles"] == ["pt"]
    assert client.get("/v1/me", headers={"X-User-Id": "not-a-uuid"}).status_code == 401
