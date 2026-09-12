"""The curated allowlist: licence mapping, the signature that makes a domain readable, and drift.

The property under test throughout is that a domain becomes readable by exactly one route — signed terms that
still match what was signed — and that every other route is closed, including the ones that look reasonable.
"""

from __future__ import annotations

import hashlib
import textwrap
from pathlib import Path

import pytest
from moveai_contracts.api import PERMISSION_OPS
from moveai_db import J
from moveai_ingestion.rights import check
from moveai_ingestion.source_policies import (
    PolicyError,
    effective_domains,
    load_licenses,
    load_publishers,
    materialise_rights_grant,
    policy_for_url,
    reject,
    sign,
    sync,
)


# ---------------------------------------------------------------- the shipped files
def test_every_shipped_policy_loads_and_every_licence_is_complete():
    licenses = load_licenses()
    policies = load_publishers(licenses=licenses)
    assert policies, "the allowlist is empty"
    for lic in licenses.values():
        assert set(lic.permissions) == set(PERMISSION_OPS)
    for p in policies:
        assert p.license_id in licenses


def test_nothing_ships_able_to_train_a_model():
    """Attribution and share-alike obligations cannot survive being absorbed into weights, so this is never on."""
    for lic in load_licenses().values():
        if lic.id not in ("cc0-1.0", "us-gov-public-domain"):
            assert lic.permissions["can_train_model"] == "denied", lic.id


def test_pubmed_central_is_not_on_the_list():
    """Per-article licences cannot be represented by a per-domain policy, and pretending otherwise asserts rights.

    This is a guard, not a preference: PMC is the most tempting entry to add and the only one on the shortlist
    where a domain policy would be wrong for most of what it covered.
    """
    domains = {p.domain for p in load_publishers()}
    assert not (domains & {"www.ncbi.nlm.nih.gov", "pmc.ncbi.nlm.nih.gov", "europepmc.org"})


# ---------------------------------------------------------------- validation
def write(tmp_path: Path, name: str, body: str) -> Path:
    p = tmp_path / name
    p.write_text(textwrap.dedent(body))
    return p


def test_a_publisher_cannot_widen_its_licence(tmp_path):
    """The whole design rests on the licence being a ceiling. If a note can widen it, nothing else matters."""
    pubs = write(
        tmp_path,
        "publishers.yaml",
        """
        publishers:
          - domain: www.jospt.org
            publisher: JOSPT
            license_id: all-rights-reserved
            policy_reference: https://www.jospt.org/terms
            overrides:
              can_redistribute: allowed
        """,
    )
    with pytest.raises(PolicyError, match="more permissive"):
        load_publishers(pubs)


def test_a_publisher_may_narrow_its_licence(tmp_path):
    pubs = write(
        tmp_path,
        "publishers.yaml",
        """
        publishers:
          - domain: medlineplus.gov
            publisher: MedlinePlus
            license_id: us-gov-public-domain
            policy_reference: https://medlineplus.gov/terms
            overrides:
              can_download_media: denied
        """,
    )
    assert load_publishers(pubs)[0].permissions["can_download_media"] == "denied"


def test_terms_must_be_published_by_the_domain_they_govern(tmp_path):
    """A policy citing someone else's page is an allowlist entry resting on nothing."""
    pubs = write(
        tmp_path,
        "publishers.yaml",
        """
        publishers:
          - domain: example-clinic.org
            publisher: Somebody
            license_id: cc-by-4.0
            policy_reference: https://creativecommons.org/licenses/by/4.0/
        """,
    )
    with pytest.raises(PolicyError, match="served by the domain"):
        load_publishers(pubs)


def test_wildcards_are_refused(tmp_path):
    pubs = write(
        tmp_path,
        "publishers.yaml",
        """
        publishers:
          - domain: "*.nih.gov"
            publisher: NIH
            license_id: us-gov-public-domain
            policy_reference: https://www.nih.gov/policies
        """,
    )
    with pytest.raises(PolicyError, match="bare host"):
        load_publishers(pubs)


# ---------------------------------------------------------------- the database gate
@pytest.fixture()
def loaded(conn, users):
    sync(conn)
    return conn


def reviewer(conn):
    return conn.execute("select id from app_user where 'rights_reviewer' = any(roles) limit 1").fetchone()["id"]


def capture(conn, domain: str, text: str = "These terms permit reuse." * 20) -> str:
    sha = hashlib.sha256(text.encode()).hexdigest()
    conn.execute(
        "update source_policy set evidence=%s, evidence_state='captured' where domain=%s",
        (J({"sha256": sha, "fetched_at": "2026-09-12T00:00:00Z", "quoted_span": text[:200]}), domain),
    )
    return sha


def test_loading_the_allowlist_makes_nothing_readable(loaded):
    assert loaded.execute("select count(*) as n from source_policy").fetchone()["n"] > 0
    assert effective_domains(loaded) == set()


def test_terms_cannot_be_signed_before_they_are_read(loaded):
    with pytest.raises(PolicyError, match="not been fetched"):
        sign(loaded, "medlineplus.gov", reviewer(loaded))


def test_signing_captured_terms_makes_a_public_domain_publisher_readable(loaded):
    capture(loaded, "medlineplus.gov")
    row = sign(loaded, "medlineplus.gov", reviewer(loaded))
    assert row["effective"] is True
    assert "medlineplus.gov" in effective_domains(loaded)
    assert row["next_review_at"] is not None


def test_signing_a_licence_that_forbids_reading_grants_nothing(loaded):
    """Signing records that the terms were read and accepted. It is not a decision to use the publisher."""
    capture(loaded, "www.jospt.org")
    row = sign(loaded, "www.jospt.org", reviewer(loaded))
    assert row["review_state"] == "signed"
    assert row["effective"] is False
    assert "www.jospt.org" not in effective_domains(loaded)


def test_changed_terms_revoke_readability_without_anyone_noticing(loaded):
    capture(loaded, "www.cdc.gov")
    assert sign(loaded, "www.cdc.gov", reviewer(loaded))["effective"] is True
    capture(loaded, "www.cdc.gov", text="The terms have been rewritten." * 20)
    row = loaded.execute("select * from source_policy where domain='www.cdc.gov'").fetchone()
    assert row["effective"] is False
    assert row["review_state"] == "pending", "the signature should not survive the text it covered"
    assert "www.cdc.gov" not in effective_domains(loaded)


def test_changing_a_permission_after_signing_invalidates_the_signature(loaded):
    capture(loaded, "www.niams.nih.gov")
    sign(loaded, "www.niams.nih.gov", reviewer(loaded))
    loaded.execute("update source_policy set can_store_fulltext='denied' where domain='www.niams.nih.gov'")
    row = loaded.execute("select * from source_policy where domain='www.niams.nih.gov'").fetchone()
    assert row["review_state"] == "pending"
    assert row["effective"] is False


def test_rejecting_a_publisher_records_the_reason(loaded):
    row = reject(loaded, "orthoinfo.aaos.org", reviewer(loaded), "needs a licence agreement with the AAOS")
    assert row["review_state"] == "rejected"
    assert "AAOS" in row["review_note"]
    assert row["effective"] is False


def test_reloading_the_files_leaves_signatures_alone(loaded):
    capture(loaded, "medlineplus.gov")
    sign(loaded, "medlineplus.gov", reviewer(loaded))
    result = sync(loaded)
    assert result["added"] == [] and result["updated"] == []
    assert "medlineplus.gov" in effective_domains(loaded)


def test_a_subdomain_does_not_inherit_its_parents_policy(loaded):
    capture(loaded, "www.nhs.uk")
    sign(loaded, "www.nhs.uk", reviewer(loaded))
    assert policy_for_url(loaded, "https://digital.nhs.uk/data") is None
    assert policy_for_url(loaded, "https://www.nhs.uk/conditions/x") is not None


# ---------------------------------------------------------------- rights grants derived from a policy
def source_version(conn, url: str):
    src = conn.execute(
        "insert into source(canonical_url, source_type, allowlist_state) values (%s,'html','pending') returning id",
        (url,),
    ).fetchone()["id"]
    return conn.execute(
        "insert into source_version(source_id, final_url, pipeline_state) values (%s,%s,'discovered') returning id",
        (src, url),
    ).fetchone()["id"]


def test_an_unsigned_policy_yields_a_grant_that_blocks_everything(loaded):
    svid = source_version(loaded, "https://medlineplus.gov/frozen-shoulder.html")
    grant = materialise_rights_grant(loaded, svid, "https://medlineplus.gov/frozen-shoulder.html")
    assert grant is not None, "the reviewer needs to see which publisher is outstanding, not a blank"
    assert all(grant[op] in ("unknown", "denied") for op in PERMISSION_OPS)
    assert check(grant, "can_fetch").allowed is False


def test_a_signed_policy_yields_a_grant_that_carries_its_permissions(loaded):
    capture(loaded, "medlineplus.gov")
    sign(loaded, "medlineplus.gov", reviewer(loaded))
    url = "https://medlineplus.gov/frozen-shoulder.html"
    grant = materialise_rights_grant(loaded, source_version(loaded, url), url)
    assert check(grant, "can_fetch").allowed is True
    assert check(grant, "can_process_with_model").allowed is True
    # The publisher override in the shipped file: A.D.A.M. illustrations are licensed, not public domain.
    assert check(grant, "can_download_media").allowed is False
    assert grant["can_train_model"] == "denied"
    assert grant["permission_evidence"]["license_id"] == "us-gov-public-domain"
    assert grant["source_policy_id"] is not None


def test_an_unknown_domain_produces_no_grant_at_all(loaded):
    url = "https://some-blog.example/exercises"
    assert materialise_rights_grant(loaded, source_version(loaded, url), url) is None
    # A missing grant already blocks every operation, so there is nothing further to assert about it.
    assert check(None, "can_fetch").allowed is False
