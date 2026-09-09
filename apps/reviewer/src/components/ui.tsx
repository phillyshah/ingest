import type { ReactNode } from "react";

export function Badge({ kind, children }: { kind?: "ok" | "warn" | "bad" | "hold" | "info"; children: ReactNode }) {
  return <span className={`badge ${kind ?? ""}`}>{children}</span>;
}

export function stateKind(s: string): "ok" | "warn" | "bad" | "hold" | "info" | undefined {
  if (["approved", "published", "succeeded", "complete", "met", "allowed", "draft_ready"].includes(s)) return "ok";
  if (["pending_review", "queued", "running", "pt_review", "needs_assessment", "unknown"].includes(s)) return "info";
  if (["rights_hold", "conflict_hold", "unsigned_placeholder", "paused", "needs_attention", "warning", "reference_only"].includes(s)) return "hold";
  if (["failed", "dead_letter", "rejected", "withdrawn", "denied", "blocked_for_clinical_review", "cancelled", "invalidated", "closed_incomplete"].includes(s)) return "bad";
  return undefined;
}

export function KV({ items }: { items: [string, ReactNode][] }) {
  return <dl className="kv">{items.map(([k, v]) => <div key={k} style={{ display: "contents" }}><dt>{k}</dt><dd>{v ?? "—"}</dd></div>)}</dl>;
}

export function Err({ e }: { e: unknown }) {
  if (!e) return null;
  const m = e instanceof Error ? e.message : String(e);
  return <div className="error">{m}</div>;
}

export function fmt(d?: string | null): string {
  return d ? new Date(d).toLocaleString() : "—";
}
