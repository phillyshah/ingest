import { useEffect, useState } from "react";
import { CURRENT, RELEASES, unseen, type ChangeKind } from "../changelog";

const KEY = "moveai.lastSeenVersion";
const LABEL: Record<ChangeKind, string> = { added: "New", changed: "Changed", fixed: "Fixed" };

function read(): string | null {
  try {
    return localStorage.getItem(KEY);
  } catch {
    return null; // private window, blocked storage — the panel still works, it just cannot remember.
  }
}

/**
 * Footer version and release notes.
 *
 * `build` is the commit actually serving, which is a different question from the product version: the version
 * says what changed for the reader, the commit says exactly which code answered them. Both are shown because
 * after a deploy only the second one can settle "did my change actually go out".
 */
export function WhatsNew({ build }: { build?: { commit: string; environment: string; built_at: string } | null }) {
  const [open, setOpen] = useState(false);
  const [lastSeen, setLastSeen] = useState<string | null>(read);
  const fresh = unseen(lastSeen);

  useEffect(() => {
    // First visit: record where they came in, so the dot marks genuinely new releases rather than the whole history.
    if (lastSeen === null) {
      try {
        localStorage.setItem(KEY, CURRENT.version);
      } catch {
        /* storage unavailable */
      }
      setLastSeen(CURRENT.version);
    }
  }, [lastSeen]);

  const show = () => {
    setOpen(true);
    try {
      localStorage.setItem(KEY, CURRENT.version);
    } catch {
      /* storage unavailable */
    }
    setLastSeen(CURRENT.version);
  };

  return (
    <>
      <footer className="footer">
        <span className="muted small">
          MoveAI Ingest <span className="mono">v{CURRENT.version}</span>
          {build && (
            <>
              {" · "}
              <span title={`built ${build.built_at}`}>
                {build.environment} <span className="mono">{build.commit}</span>
              </span>
            </>
          )}
        </span>
        <span className="spacer" />
        <button className="small" onClick={show} data-testid="whats-new">
          What&rsquo;s new{fresh.length > 0 && <span className="dot" aria-label={`${fresh.length} new`} />}
        </button>
      </footer>

      {open && (
        <dialog open className="whatsnew" data-testid="whats-new-panel">
          <div className="row">
            <h2 style={{ margin: 0 }}>What&rsquo;s new</h2>
            <span className="spacer" />
            <button onClick={() => setOpen(false)}>Close</button>
          </div>
          {RELEASES.map((r) => (
            <section key={r.version} style={{ marginTop: 16 }}>
              <h3 style={{ marginBottom: 2 }}>
                <span className="mono">v{r.version}</span> — {r.headline}
              </h3>
              <div className="muted small">{new Date(r.date + "T00:00:00Z").toLocaleDateString(undefined, { dateStyle: "medium" })}</div>
              <ul style={{ marginTop: 8 }}>
                {r.changes.map((c, i) => (
                  <li key={i}>
                    <span className={`badge ${c.kind === "fixed" ? "warn" : c.kind === "added" ? "ok" : ""}`}>{LABEL[c.kind]}</span>{" "}
                    {c.text}
                  </li>
                ))}
              </ul>
            </section>
          ))}
        </dialog>
      )}
    </>
  );
}
