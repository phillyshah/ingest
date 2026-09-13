// What's new, shown in the footer.
//
// This is the product's release notes, written for the people using the system, not a git log. Every build that
// changes something a user would notice gets an entry here, and the newest entry is the version the footer shows.
//
// Rules that keep it honest:
//   * Newest first. The footer and the "new since you last looked" dot both depend on it.
//   * Describe the change from the user's side. "Sign in with a username" not "add /v1/dev-login".
//   * Leave out anything invisible to a user — refactors, CI, infrastructure — unless it changed what they see.
//   * A version appears here only once it is merged, so what the footer shows is what is actually running.

export type ChangeKind = "added" | "changed" | "fixed";

export interface Change {
  kind: ChangeKind;
  text: string;
}

export interface Release {
  version: string;
  date: string; // ISO date, the day it merged
  headline: string;
  changes: Change[];
}

export const RELEASES: Release[] = [
  {
    version: "0.8.2",
    date: "2026-09-13",
    headline: "Uploading a PDF source no longer fails",
    changes: [
      {
        kind: "fixed",
        text: "Uploading a PDF on the Sources page failed with a server error every time on the deployed environment: the API could not write the file where it needed to. Uploads now save correctly and flow into the review queue like any other source.",
      },
    ],
  },
  {
    version: "0.8.1",
    date: "2026-09-13",
    headline: "Fixes from the September review",
    changes: [
      {
        kind: "fixed",
        text: "A stored exercise photo could never reach a plan: the rule that a picture must pass technique review before a patient sees it has always existed, but there was no way to record that review. A PT can now mark a photo as showing the exercise correctly (or not) from the review queue, and a variant with an older reference link no longer hides its newer photo.",
      },
      {
        kind: "fixed",
        text: "A dose that claims to come from a source is now checked: the cited passage must exist, must be a dose, and must come from the same document as the exercise. Previously any identifier was accepted. Plan edits can no longer replace an exercise's approved instructions with pasted text.",
      },
      {
        kind: "fixed",
        text: "Exercises read by a campaign are now filed under the body region of the condition being covered instead of \"unknown\", so the catalog can find them by region.",
      },
      {
        kind: "fixed",
        text: "Reading real publishers' pages: the fetcher now sends the request headers publishers expect (several refused it before), and a page that redirects to a publisher's new domain is parked as \"needs a policy for that domain\" instead of failing with an unhelpful error.",
      },
      {
        kind: "changed",
        text: "A campaign's \"where it will look\" list now says plainly that a signed publisher is one the system may read when given a URL, not one it will search — automatic discovery is not built yet.",
      },
      {
        kind: "fixed",
        text: "The open/closed-chain setting on an exercise in a content pack was silently discarded on install; it is now kept.",
      },
    ],
  },
  {
    version: "0.8.0",
    date: "2026-09-12",
    headline: "Typing something close now offers the exact match",
    changes: [
      {
        kind: "added",
        text: "A campaign's \"what should it cover?\" field now suggests the closest condition in the catalog when what you typed is close but not exact — a typo, or a looser description. Nothing is applied on its own: you pick \"Use this\" to accept a suggestion, same as everywhere else in the system that nothing is guessed silently.",
      },
    ],
  },
  {
    version: "0.7.0",
    date: "2026-09-12",
    headline: "Upload your own PDFs, photos and all",
    changes: [
      {
        kind: "added",
        text: "Upload a PDF protocol sheet or handout directly from the Sources page. It runs through the same pipeline as anything read from the web and lands in the review queue — no publisher terms to accept, since nothing is being read from someone else's site.",
      },
      {
        kind: "added",
        text: "Exercise photos embedded in an uploaded PDF are pulled out and attached to the right exercise automatically. If a page has more than one exercise on it, the photo is left for a PT to attach by hand rather than guessed at.",
      },
      {
        kind: "added",
        text: "A stored photo now actually shows up in the review queue, next to the exercise it belongs to, instead of a link.",
      },
    ],
  },
  {
    version: "0.6.0",
    date: "2026-09-12",
    headline: "A pathway for a plain MCL sprain",
    changes: [
      {
        kind: "added",
        text: "A grade 1–3 MCL sprain treated without surgery is now recognised. It previously matched nothing — the only MCL pathway in the system was for a surgically repaired tear, a different injury with a different plan, so the system correctly refused to guess rather than routing a sprain into a surgery pathway. It is still an unsigned placeholder like every other pathway: no doses until a clinical lead reviews it.",
      },
      {
        kind: "fixed",
        text: "A diagnosis code shared between two different pathways (as a knee-sprain code is, whether or not it was later treated surgically) no longer makes a campaign refuse to start as \"ambiguous.\" The code alone was never enough to tell the two apart — what you write about the injury is.",
      },
    ],
  },
  {
    version: "0.5.3",
    date: "2026-09-12",
    headline: "The default sign-in can do everything, for now",
    changes: [
      {
        kind: "changed",
        text: "Signed in as the default admin user, you can now accept publishers' terms, approve exercises and plans, and everything else — no more switching roles first. This is a staging convenience for the single-operator period; it goes away once real per-person sign-in and roles are added.",
      },
    ],
  },
  {
    version: "0.5.2",
    date: "2026-09-12",
    headline: "Clearer about who can accept a publisher's terms",
    changes: [
      { kind: "fixed", text: "The Sources page now says plainly when your current role cannot accept or reject a publisher, and which role to switch to, instead of a easy-to-miss note at the bottom." },
      { kind: "changed", text: "For a rights reviewer or clinical lead, a publisher's terms open automatically so the accept/reject buttons are visible straight away, rather than hidden behind a details toggle." },
    ],
  },
  {
    version: "0.5.1",
    date: "2026-09-12",
    headline: "Reading real web pages, and a way to confirm a campaign",
    changes: [
      { kind: "fixed", text: "Text was not being read from real web pages at all. Only the outermost layer of a page was looked at, and since every real site nests its content, ingesting one produced an empty result that looked just like a page with nothing in it. Every kind of page is now read properly." },
      { kind: "added", text: "A campaign waiting to be confirmed now shows what the system understood you to be asking for, with a button to confirm it. Previously that could only be done while first creating the campaign, so a saved draft could never be started." },
      { kind: "changed", text: "The board says whether a draft is waiting for confirmation or ready to start, instead of showing the same message for both." },
      { kind: "fixed", text: "When a publisher's terms page cannot be read, the reason now says what happened — refused, moved, or rate limited — rather than reporting it as an empty page." },
    ],
  },
  {
    version: "0.5.0",
    date: "2026-09-12",
    headline: "A curated list of publishers the system may read",
    changes: [
      { kind: "added", text: "A Sources page listing every publisher the system may read, the licence each one publishes under, and exactly what that licence permits — down to whether a picture may be shown to a patient." },
      { kind: "added", text: "Thirteen publishers to start with, including the US federal health agencies, the NHS, NICE and the VA/DoD clinical practice guidelines." },
      { kind: "added", text: "A publisher becomes readable only after its licence terms have been fetched from its own site and a rights reviewer has accepted that exact wording. Accepting terms nobody has read is not possible." },
      { kind: "added", text: "If a publisher rewrites its terms, the acceptance lapses on its own and that publisher stops being readable until someone reads the new wording. Terms are re-read weekly." },
      { kind: "changed", text: "A campaign that cannot reach a source now says which publisher is involved and what it is waiting for, instead of “pending allowlist approval”." },
      { kind: "changed", text: "Campaigns can reach an accepted publisher without anyone entering that source by hand first." },
    ],
  },
  {
    version: "0.4.0",
    date: "2026-09-12",
    headline: "Easier to sign in, and far less to fill in",
    changes: [
      { kind: "changed", text: "Sign in with a username instead of pasting two long identifiers and picking a role." },
      { kind: "changed", text: "Creating a campaign asks three questions instead of eleven. Everything else moved under Advanced with its existing defaults." },
      { kind: "changed", text: "Priority is now High, Medium or Low rather than a number." },
      { kind: "changed", text: "Plain-English labels: the old “spend_usd” column is now “Spent”, and “max usd” is “Spend cap”. Time limits are in minutes and document sizes in MB." },
      { kind: "added", text: "Campaigns can be deleted. They disappear from the board; their history is kept, because the record of what was ingested cannot be erased." },
      { kind: "added", text: "This What’s new panel, plus a version number and the running build shown in the footer." },
      { kind: "fixed", text: "Campaign spend was being charged at roughly 50% over the real rate. Extraction now runs on the cheapest current model and is priced from that model’s own published rate." },
      { kind: "added", text: "Extraction can run through OpenRouter on an open-weight model, billed at the rate OpenRouter reports for each call." },
      { kind: "fixed", text: "Errors from the server are reported as what they are. A server error used to appear as a confusing \u201cJSON Parse error\u201d that pointed at the wrong thing entirely." },
      { kind: "fixed", text: "Deploying a version whose database changes have not been applied is now refused, leaving the working version running instead of taking the site down." },
    ],
  },
  {
    version: "0.3.0",
    date: "2026-09-12",
    headline: "Live at ingest.phillyshah.com",
    changes: [
      { kind: "added", text: "The system is deployed and reachable over HTTPS." },
      { kind: "added", text: "A single deploy-ingest command on the server pulls the latest version, rebuilds and restarts, and rolls back if the new build is unhealthy." },
      { kind: "fixed", text: "The API is reachable from the browser. It previously loaded the page while refusing every request the page made." },
    ],
  },
  {
    version: "0.2.0",
    date: "2026-09-12",
    headline: "Database locked down and loaded",
    changes: [
      { kind: "added", text: "Schema deployed to Supabase with terminology and the three placeholder clinical packs." },
      { kind: "added", text: "Access is invitation-only: a Supabase account grants nothing until an administrator gives it a role." },
      { kind: "fixed", text: "Closed a gap that would have left the database readable by anyone holding the public key." },
    ],
  },
  {
    version: "0.1.0",
    date: "2026-09-11",
    headline: "First working engine",
    changes: [
      { kind: "added", text: "Ingestion campaigns with a Kanban board, funnel counts, spend against cap, and live updates." },
      { kind: "added", text: "Source ingestion with rights checks, provenance on every claim, and duplicate proposals that are never merged automatically." },
      { kind: "added", text: "Exercise catalog, PT review queue, and plan options for a case." },
      { kind: "added", text: "Plan drafting that routes to needs assessment, blocked for clinical review, or draft ready — and never invents a dose." },
    ],
  },
];

export const CURRENT = RELEASES[0];

/** Releases the reader has not seen, newest first. Empty when they are up to date. */
export function unseen(lastSeenVersion: string | null): Release[] {
  if (!lastSeenVersion) return [];
  const i = RELEASES.findIndex((r) => r.version === lastSeenVersion);
  // An unrecognised stored version means the app moved on in a way we cannot place; treat it as caught up rather
  // than claiming every release is new.
  return i <= 0 ? [] : RELEASES.slice(0, i);
}
