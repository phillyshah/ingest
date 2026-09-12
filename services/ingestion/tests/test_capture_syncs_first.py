"""`capture` must sync from publishers.yaml before fetching, or a corrected URL never gets used.

What happened: two policy_reference URLs were fixed in publishers.yaml and merged. `capture` was run straight
after, with no `load` in between, and silently re-fetched the OLD urls still sitting in source_policy — reporting
the same 404 a second time with no sign that the fix in the file was never applied. `load` and `capture` are
different scripts.py subcommands; nothing forced the first to run before the second.

The fix is capture calling sync() itself. These tests are against that behaviour directly, not through the CLI,
because the CLI's `capture` also makes real HTTP requests — the property under test is entirely about capture
picking up a database row it wrote itself, so a fake httpx transport plus a real database is enough.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path
from unittest.mock import MagicMock, patch

import httpx
import moveai_ingestion.source_policies as source_policies
import pytest
from moveai_ingestion.source_policies import load_publishers, sync

_spec = importlib.util.spec_from_file_location(
    "moveai_source_policies_cli", Path(__file__).resolve().parents[3] / "scripts" / "source_policies.py"
)
cli = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(cli)


def _args(domain: str | None = None) -> MagicMock:
    ns = MagicMock()
    ns.domain = domain
    return ns


@pytest.fixture()
def loaded(conn, users):
    # cmd_capture commits for real — it is a script meant to run once against a live database, not a request
    # handler inside the per-test rollback the rest of the suite relies on. A real commit here would permanently
    # write test fixtures into the shared local test database, corrupting every test that runs after this one in
    # the same session. Make commit a no-op: everything cmd_capture writes stays inside this test's transaction,
    # where the `conn` fixture's own rollback at teardown discards it exactly like every other test's writes.
    conn.commit = lambda: None
    sync(conn)
    return conn


def _client_stub(handler):
    """A drop-in for httpx.Client that ignores connection kwargs and always uses the fake transport.

    Not `patch.object(httpx, "Client", lambda **kw: httpx.Client(...))`: that replaces the name `httpx.Client`
    globally, so the *replacement* calling `httpx.Client(...)` recurses into itself.
    """
    real_client = httpx.Client

    def make(**kw):
        kw = {k: v for k, v in kw.items() if k != "transport"}
        return real_client(transport=httpx.MockTransport(handler), **kw)

    return make


def test_capture_picks_up_a_url_fixed_in_the_file_without_a_separate_load(loaded, monkeypatch):
    # Simulate the exact sequence that broke: the database still has the old (wrong) URL for a domain, and the
    # file on disk has since been corrected. Rather than editing the shipped file, point load_publishers at a
    # temporary copy with one URL changed, and have cli.load_publishers (used inside sync, called by cmd_capture)
    # return that.
    real = load_publishers()
    fixed_domain = "medlineplus.gov"
    corrected = [
        p if p.domain != fixed_domain else p.__class__(**{**p.__dict__, "policy_reference": "https://medlineplus.gov/corrected-terms/"})
        for p in real
    ]
    # cmd_capture calls sync(conn) with no explicit policies, so sync's own default argument is what runs — that
    # default calls load_publishers() from *its own module's* globals, not the name imported into cli. Patching
    # cli.load_publishers would patch a reference sync() never looks at.
    monkeypatch.setattr(source_policies, "load_publishers", lambda *a, **kw: corrected)

    seen_urls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen_urls.append(str(request.url))
        return httpx.Response(200, text="<html><body><p>" + "Reuse permitted. " * 40 + "</p></body></html>")

    with patch.object(httpx, "Client", _client_stub(handler)):
        rc = cli.cmd_capture(loaded, _args(domain=fixed_domain))

    assert rc == 0
    # The whole point: capture fetched the corrected URL, not whatever was in the database before this call.
    assert seen_urls == ["https://medlineplus.gov/corrected-terms/"]
    row = loaded.execute("select policy_reference, evidence_state from source_policy where domain=%s", (fixed_domain,)).fetchone()
    assert row["policy_reference"] == "https://medlineplus.gov/corrected-terms/"
    assert row["evidence_state"] == "captured"


def test_capture_with_no_file_changes_does_not_touch_signatures(loaded, monkeypatch):
    """The sync-first behaviour must be a no-op when nothing changed: it must never re-litigate a signature that
    is still valid just because capture happened to run."""
    from moveai_ingestion.source_policies import sign

    reviewer = loaded.execute("select id from app_user where 'rights_reviewer' = any(roles) limit 1").fetchone()["id"]
    loaded.execute(
        "update source_policy set evidence='{\"sha256\": \"abc\"}'::jsonb, evidence_state='captured' where domain='medlineplus.gov'"
    )
    sign(loaded, "medlineplus.gov", reviewer)
    before = loaded.execute("select review_state, signed_evidence_sha256 from source_policy where domain='medlineplus.gov'").fetchone()
    assert before["review_state"] == "signed"

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text="<html><body><p>" + "Reuse permitted. " * 40 + "</p></body></html>")

    with patch.object(httpx, "Client", _client_stub(handler)):
        cli.cmd_capture(loaded, _args(domain="www.cdc.gov"))  # capture a different domain; medlineplus untouched

    after = loaded.execute("select review_state, signed_evidence_sha256 from source_policy where domain='medlineplus.gov'").fetchone()
    assert after == before
