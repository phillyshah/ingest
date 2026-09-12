# Runbook: the curated source allowlist

**What this is.** The list of publishers the system is permitted to read, and the licence terms each one publishes
under. A campaign can fetch from a publisher on this list and from nowhere else. There is no crawling.

**Why it is not just a list of domains.** Rights `unknown` blocks the use (spec §4). An allowlist that only said
"you may fetch nih.gov" would produce a pipeline full of rights holds, because knowing we *can* reach a page says
nothing about whether we may store it, run a model over it, or show it to a patient. Each entry therefore carries
all twelve permissions, derived from the publisher's licence.

---

## The three things that must line up

A publisher is readable only when all three are true. Any one of them failing blocks the publisher, and the
database enforces this in a generated column, so no code path can work around it.

| | What it is | Where it lives |
| --- | --- | --- |
| **Licence** | What the terms permit, mapped to the twelve operations | `fixtures/source-policies/licenses.yaml` |
| **Evidence** | The publisher's terms page as actually fetched, with a hash of its text | fetched by the `capture` step |
| **Signature** | A rights reviewer read that text and accepted it | the Sources page, or the `sign` step |

The signature covers the **hash of the terms text**, not the date. So if a publisher rewrites its licence, the
hash stops matching, the signature lapses by itself, and that publisher silently stops being readable. Nobody has
to notice. That is the point.

---

## First-time setup

Everything runs from **Actions → Source allowlist → Run workflow**.

### 1. Load the list

Action: `load`. Reads the policy files into the database. Nothing becomes readable.

### 2. Read every publisher's terms

Action: `capture`. Fetches each publisher's terms page, extracts the text, stores it with its hash.

This runs on a GitHub runner because it needs the open internet. Expect a few `unreachable` results: some terms
pages are rendered client-side and extract to nothing, which is reported rather than silently accepted — signing a
blank page would be worse than not signing at all.

### 3. Accept the terms, one publisher at a time

Open **Sources** in the app, signed in as a rights reviewer or clinical lead. Each publisher shows:

- the licence and a link to it
- a link to the publisher's own terms page
- the terms text as it was fetched
- exactly what the licence permits, operation by operation

Read the terms. If they are acceptable, press **Accept these terms**. If not, press **Do not use this publisher**
and say why — an unexplained refusal gets proposed again next quarter.

The workflow can do the same thing (action `sign`, with a domain and your email) if you would rather not use the
app.

### What "accepted" does and does not mean

Accepting records that a person read the terms. It is **not** a decision to use the publisher. Accepting JOSPT's
terms leaves JOSPT unreadable, because its licence does not permit reading — and the Sources page says so.

---

## Ongoing

**Weekly.** The workflow re-captures every publisher's terms on a schedule. If a publisher changed them, the run
summary says so and that publisher is marked `drifted`: unreadable until someone reads the new wording and signs
again.

**When a campaign parks a source.** The Needs Attention card names the publisher and what it is waiting for.
Nearly always that is step 3 above.

---

## Adding a publisher

A pull request against `fixtures/source-policies/publishers.yaml`, not a form field. Deliberate: this file is the
boundary between a system that reads what it was permitted to read and a crawler.

```yaml
  - domain: www.example.org          # exact host. www.example.org and example.org are separate decisions.
    publisher: Example Foundation
    license_id: cc-by-4.0            # must exist in licenses.yaml
    policy_reference: https://www.example.org/terms   # must be served by the domain it governs
    scope_note: What the licence does not cover.
    overrides:
      can_download_media: denied     # may narrow the licence, never widen it
```

The loader refuses wildcards, refuses a terms page hosted on a different domain, and refuses an override that is
more permissive than the licence. Then `load`, `capture`, sign.

**Per-article licensing.** PubMed Central and Europe PMC are deliberately absent. Their articles carry a licence
each — some CC BY, some CC BY-NC, some publisher-specific — so one domain-level policy would be wrong for most of
what it covered. Using them needs per-document licence detection at fetch time, which does not exist yet.

---

## Command line

Same operations, against `DATABASE_URL`, for when the workflow is not convenient:

```bash
uv run python scripts/source_policies.py load
uv run python scripts/source_policies.py capture [--domain www.cdc.gov]
uv run python scripts/source_policies.py list
uv run python scripts/source_policies.py sign --domain www.cdc.gov --reviewer you@example.com
uv run python scripts/source_policies.py sign --domain www.jospt.org --reviewer you@example.com --reject --note "subscription only"
```

---

## The other allowlist

`ALLOWED_FETCH_DOMAINS` in `infra/.env` still exists and is unioned with this table. It is an operator escape
hatch for a one-off: it carries no licence, no evidence and no signature, so anything fetched through it arrives
with no rights grant and holds at the first stage anyway. Use the policy table.
