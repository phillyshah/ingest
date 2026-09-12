import { describe, expect, it } from "vitest";
import { CURRENT, RELEASES, unseen } from "./changelog";

// The footer's version, the "new since you last looked" dot, and the order of the panel all depend on these
// invariants. They are cheap to assert and easy to break by adding an entry in the wrong place.
describe("changelog", () => {
  it("is newest-first, which is what the footer version depends on", () => {
    const dates = RELEASES.map((r) => r.date);
    expect([...dates].sort().reverse()).toEqual(dates);
    expect(CURRENT).toBe(RELEASES[0]);
  });

  it("has no duplicate versions", () => {
    const versions = RELEASES.map((r) => r.version);
    expect(new Set(versions).size).toBe(versions.length);
  });

  it("gives every release a headline and at least one change", () => {
    for (const r of RELEASES) {
      expect(r.headline.trim()).not.toBe("");
      expect(r.changes.length).toBeGreaterThan(0);
      expect(/^\d+\.\d+\.\d+$/.test(r.version)).toBe(true);
      expect(/^\d{4}-\d{2}-\d{2}$/.test(r.date)).toBe(true);
      for (const c of r.changes) expect(c.text.trim()).not.toBe("");
    }
  });

  it("describes changes for a reader, not a developer", () => {
    // A weak check on purpose, but it catches the commonest slip: pasting an endpoint or a filename into notes
    // that a clinician is meant to read.
    const jargon = /\/v1\/|\.tsx?\b|\.py\b|localStorage|postgres/i;
    for (const r of RELEASES) {
      for (const c of r.changes) expect(c.text).not.toMatch(jargon);
    }
  });
});

describe("unseen", () => {
  it("is empty for a first-time reader, so nobody is shown the whole history as new", () => {
    expect(unseen(null)).toEqual([]);
  });

  it("is empty when the reader is up to date", () => {
    expect(unseen(CURRENT.version)).toEqual([]);
  });

  it("returns only the releases published since the reader last looked", () => {
    const older = RELEASES[2].version;
    expect(unseen(older).map((r) => r.version)).toEqual([RELEASES[0].version, RELEASES[1].version]);
  });

  it("treats an unrecognised stored version as caught up rather than as everything being new", () => {
    expect(unseen("99.0.0")).toEqual([]);
  });
});
