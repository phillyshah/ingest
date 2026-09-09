import { describe, expect, it } from "vitest";
import { columnOf, dragOutcome, funnel, staleness } from "./kanban";

const card = (over: Partial<Parameters<typeof dragOutcome>[0]> = {}) => ({
  id: "c1", lifecycle: "draft" as const, control: "active" as const, priority: 100, allowed_actions: ["start"], ...over,
});

describe("drag rules (spec §21C)", () => {
  it("draft -> queued starts when scope is confirmed", () => {
    expect(dragOutcome(card(), "queued")).toEqual({ kind: "start" });
    expect(dragOutcome(card({ allowed_actions: [] }), "queued").kind).toBe("refused");
  });
  it("never allows dragging to complete or skipping review", () => {
    for (const target of ["running", "needs_attention", "pt_review", "complete"]) {
      expect(dragOutcome(card(), target).kind).toBe("refused");
      expect(dragOutcome(card({ lifecycle: "running" }), target).kind).toBe(target === "running" ? "refused" : "refused");
    }
  });
  it("reprioritizes within draft/queued only", () => {
    expect(dragOutcome(card({ lifecycle: "queued" }), "queued", "c9")).toEqual({ kind: "reprioritize", before: "c9" });
    expect(dragOutcome(card({ lifecycle: "pt_review" }), "pt_review").kind).toBe("refused");
  });
  it("cancelled cards are immovable and closed_incomplete shows under needs attention", () => {
    expect(dragOutcome(card({ control: "cancelled" }), "queued").kind).toBe("refused");
    expect(columnOf(card({ lifecycle: "closed_incomplete" }))).toBe("needs_attention");
  });
});

describe("progress display", () => {
  it("shows funnel counts, never a fabricated percent", () => {
    const f = funnel({ sources_discovered: 3, sources_processed: 1, awaiting_review: 4 });
    expect(f.find((x) => x.label === "sources")?.value).toBe(3);
    expect(f.some((x) => /percent|%/.test(x.label))).toBe(false);
  });
  it("staleness prefers disconnection, then heartbeat", () => {
    expect(staleness(new Date().toISOString(), true, false)).toMatch(/disconnected/);
    expect(staleness(new Date().toISOString(), true, true)).toMatch(/heartbeat/);
    expect(staleness(new Date(Date.now() - 5000).toISOString(), false, true)).toMatch(/updated \ds ago/);
  });
});
