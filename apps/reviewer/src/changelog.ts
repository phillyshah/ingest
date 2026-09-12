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
