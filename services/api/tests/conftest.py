from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from moveai_api.db import get_conn
from moveai_api.main import app
from moveai_planner.seed import seed


@pytest.fixture()
def seeded(conn):
    return seed(conn)


@pytest.fixture()
def client(conn, seeded):
    def _conn():
        yield conn

    app.dependency_overrides[get_conn] = _conn
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.clear()


class As:
    def __init__(self, client: TestClient, seeded: dict, who: str, role: str | None = None):
        self.c, self.s = client, seeded
        self.h = {"X-User-Id": str(seeded[who]), "X-Tenant-Id": str(seeded["tenant_id"])}
        if role:
            self.h["X-Role"] = role

    def get(self, url, **kw):
        return self.c.get("/v1" + url, headers={**self.h, **kw.pop("headers", {})}, **kw)

    def post(self, url, json=None, **kw):
        return self.c.post("/v1" + url, json=json, headers={**self.h, **kw.pop("headers", {})}, **kw)

    def patch(self, url, json=None, **kw):
        return self.c.patch("/v1" + url, json=json, headers={**self.h, **kw.pop("headers", {})}, **kw)

    def delete(self, url, **kw):
        return self.c.delete("/v1" + url, headers={**self.h, **kw.pop("headers", {})}, **kw)


@pytest.fixture()
def admin(client, seeded):
    return As(client, seeded, "admin")


@pytest.fixture()
def pt(client, seeded):
    return As(client, seeded, "pt")


@pytest.fixture()
def pt2(client, seeded):
    return As(client, seeded, "pt2")


@pytest.fixture()
def lead(client, seeded):
    return As(client, seeded, "lead")


@pytest.fixture()
def rights(client, seeded):
    return As(client, seeded, "rights")


@pytest.fixture()
def integration(client, seeded):
    return As(client, seeded, "integration")
