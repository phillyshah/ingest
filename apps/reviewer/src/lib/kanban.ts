// Board semantics (spec §21C). Columns come from the server-derived lifecycle; drag can only request permitted
// transitions, and the server independently enforces them.
export const COLUMNS = [
  { key: "draft", label: "Draft / Backlog" },
  { key: "queued", label: "Queued" },
  { key: "running", label: "Running" },
  { key: "needs_attention", label: "Needs Attention" },
  { key: "pt_review", label: "PT Review" },
  { key: "complete", label: "Complete" },
] as const;

export type Lifecycle = (typeof COLUMNS)[number]["key"] | "closed_incomplete";

export interface CardLike {
  id: string;
  lifecycle: Lifecycle;
  control: "active" | "paused" | "cancelled";
  priority: number;
  allowed_actions: string[];
}

export type DragOutcome = { kind: "start" } | { kind: "reprioritize"; before?: string } | { kind: "refused"; reason: string };

export function dragOutcome(card: CardLike, targetColumn: string, targetBeforeId?: string): DragOutcome {
  if (card.control === "cancelled") return { kind: "refused", reason: "cancelled campaigns cannot be moved" };
  if (targetColumn === card.lifecycle) {
    if (card.lifecycle === "queued" || card.lifecycle === "draft") return { kind: "reprioritize", before: targetBeforeId };
    return { kind: "refused", reason: "reordering is only meaningful in Draft and Queued" };
  }
  if (card.lifecycle === "draft" && targetColumn === "queued") {
    return card.allowed_actions.includes("start") ? { kind: "start" } : { kind: "refused", reason: "scope must be confirmed before starting" };
  }
  return { kind: "refused", reason: `cards cannot be moved to ${targetColumn}: status is derived from real events` };
}

export function columnOf(card: CardLike): string {
  return card.lifecycle === "closed_incomplete" ? "needs_attention" : card.lifecycle;
}

export function funnel(counts: Record<string, number>): { label: string; value: number }[] {
  return [
    { label: "sources", value: counts.sources_discovered ?? 0 },
    { label: "processed", value: counts.sources_processed ?? 0 },
    { label: "new variants", value: counts.new_variants ?? 0 },
    { label: "reused", value: counts.reused_variants ?? 0 },
    { label: "dupes", value: counts.duplicates ?? 0 },
    { label: "evidence", value: counts.evidence_linked ?? 0 },
    { label: "awaiting review", value: counts.awaiting_review ?? 0 },
    { label: "approved", value: counts.approved ?? 0 },
    { label: "published", value: counts.published ?? 0 },
  ];
}

export function staleness(lastUpdate: string | null, heartbeatStale: boolean, connected: boolean, now = Date.now()): string {
  if (!connected) return "disconnected: showing last known state";
  if (heartbeatStale) return "worker heartbeat expired";
  if (!lastUpdate) return "no updates yet";
  const s = Math.round((now - new Date(lastUpdate).getTime()) / 1000);
  return s < 60 ? `updated ${s}s ago` : `updated ${Math.round(s / 60)}m ago`;
}
