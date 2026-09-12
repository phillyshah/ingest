import { useState } from "react";
import { useSourcePolicies, useSourcePolicyDecision, type SourcePolicy } from "../api/hooks";
import { useAuth, hasRole } from "../auth";
import { Badge, Err, fmt } from "../components/ui";

// The allowlist, as a page rather than a mystery. A campaign that reads nothing is nearly always a publisher
// waiting on this screen, and until now the only way to find that out was to read a log.

const PERM_LABELS: Record<string, string> = {
  can_fetch: "Read the page",
  can_store_fulltext: "Keep the full text",
  can_store_excerpt: "Keep quoted excerpts",
  can_embed_for_search: "Index for search",
  can_process_with_model: "Run extraction over it",
  can_store_transcript: "Keep transcripts",
  can_download_media: "Download images and video",
  can_display_to_clinician: "Show to a clinician",
  can_display_to_patient: "Show to a patient",
  can_redistribute: "Redistribute",
  can_transform: "Adapt or rewrite",
  can_train_model: "Train a model on it",
};

function permKind(v: string): "ok" | "bad" | "warn" {
  return v === "allowed" ? "ok" : v === "denied" ? "bad" : "warn";
}

function Policy({ p }: { p: SourcePolicy }) {
  const { session } = useAuth();
  const decide = useSourcePolicyDecision();
  const canDecide = hasRole(session, "rights_reviewer", "clinical_lead");
  // Open by default for whoever can actually act on this page. The buttons live inside this section because
  // signing without the terms on screen would make the signature a formality — so a reviewer has to see them
  // expanded to do anything anyway; not expanding automatically just hid the one thing they came here to do.
  const [open, setOpen] = useState(canDecide);
  const [note, setNote] = useState("");
  // Signing means "I read these terms and accept them", so the terms have to be on screen to sign. Enabling the
  // button before anything was fetched would make the signature a formality, which is the one thing it must not be.
  const signable = p.evidence_state === "captured" || p.evidence_state === "drifted";

  return (
    <div className="panel" style={{ marginBottom: 10 }}>
      <div className="row" style={{ justifyContent: "space-between", alignItems: "baseline" }}>
        <div>
          <strong>{p.publisher}</strong> <span className="mono small muted">{p.domain}</span>
        </div>
        <Badge kind={p.effective ? "ok" : p.review_state === "rejected" ? "bad" : "warn"}>
          {p.effective ? "readable" : p.review_state === "rejected" ? "rejected" : "not readable"}
        </Badge>
      </div>

      <div className="small" style={{ marginTop: 6 }}>
        {p.license.url ? (
          <a href={p.license.url} target="_blank" rel="noreferrer">{p.license.name}</a>
        ) : (
          p.license.name
        )}
        {" · "}
        <a href={p.policy_reference} target="_blank" rel="noreferrer">their terms page</a>
        {p.terms_fetched_at && <span className="muted"> · last read {fmt(p.terms_fetched_at)}</span>}
      </div>

      {p.blocked_by && <div className="notice small" style={{ marginTop: 8 }}>{p.blocked_by}</div>}
      {p.scope_note && <p className="small muted" style={{ marginTop: 8 }}>{p.scope_note}</p>}

      <button className="small" style={{ marginTop: 8 }} onClick={() => setOpen(!open)}>
        {open ? "Hide" : "What this licence allows"}
      </button>

      {open && (
        <>
          {p.license.summary && <p className="small" style={{ marginTop: 8 }}>{p.license.summary}</p>}
          <div className="row" style={{ flexWrap: "wrap", gap: 6, marginTop: 8 }}>
            {Object.entries(p.permissions).map(([op, v]) => (
              <Badge key={op} kind={permKind(v)}>{PERM_LABELS[op] ?? op}: {v}</Badge>
            ))}
          </div>
          {p.license.notes.length > 0 && (
            <ul className="small muted" style={{ marginTop: 8 }}>
              {p.license.notes.map((n) => <li key={n}>{n}</li>)}
            </ul>
          )}
          {p.terms_excerpt && (
            <>
              <h3 className="small" style={{ marginTop: 10 }}>The terms as fetched</h3>
              <pre className="small" style={{ whiteSpace: "pre-wrap" }}>{p.terms_excerpt}…</pre>
            </>
          )}
          {canDecide && (
            <>
              <Err e={decide.error} />
              <label className="f" style={{ marginTop: 8 }}>
                Note<input value={note} onChange={(e) => setNote(e.target.value)} placeholder="recorded with the decision" />
              </label>
              <div className="row" style={{ marginTop: 8 }}>
                <button
                  className="primary"
                  disabled={!signable || decide.isPending}
                  title={signable ? undefined : "Nobody has read this publisher's terms yet"}
                  onClick={() => decide.mutate({ domain: p.domain, decision: "sign", note: note || undefined })}
                >
                  Accept these terms
                </button>
                <button
                  className="danger"
                  disabled={!note.trim() || decide.isPending}
                  title={note.trim() ? undefined : "Say why, so this does not get proposed again next quarter"}
                  onClick={() => decide.mutate({ domain: p.domain, decision: "reject", note })}
                >
                  Do not use this publisher
                </button>
              </div>
            </>
          )}
        </>
      )}

      {p.reviewed_at && (
        <p className="small muted" style={{ marginTop: 8 }}>
          {p.review_state} {fmt(p.reviewed_at)}{p.review_note ? ` — ${p.review_note}` : ""}
        </p>
      )}
    </div>
  );
}

export default function Sources() {
  const q = useSourcePolicies();
  const { session } = useAuth();
  if (q.error) return <Err e={q.error} />;
  if (!q.data) return <p className="muted">loading…</p>;
  const readable = q.data.items.filter((p) => p.effective);
  const rest = q.data.items.filter((p) => !p.effective);

  return (
    <>
      <h1>Sources</h1>
      <p className="muted">
        The publishers this system may read, and on what terms. A campaign can only reach a publisher listed as
        readable below — there is no crawling of anything else.
      </p>

      {!hasRole(session, "rights_reviewer", "clinical_lead") && (
        <div className="error" style={{ marginBottom: 10 }}>
          <b>You cannot accept or reject anything on this page right now.</b> Your current role is{" "}
          <span className="mono">{session?.role}</span>, and accepting a publisher's licence requires{" "}
          <span className="mono">rights_reviewer</span> or <span className="mono">clinical_lead</span>. Switch to
          one of those in the role dropdown at the top of the page — your account holds every role, this just picks
          which one is active — then come back here.
        </div>
      )}

      <div className="notice">
        <b>{q.data.readable} of {q.data.total} publishers can be read.</b>{" "}
        A publisher becomes readable when its licence terms have been fetched from its own site <i>and</i> a rights
        reviewer has accepted that exact text. If a publisher rewrites its terms, the acceptance lapses by itself
        and the publisher stops being readable until someone reads the new wording.
      </div>

      {readable.length > 0 && <h2>Readable</h2>}
      {readable.map((p) => <Policy key={p.domain} p={p} />)}

      <h2>Not readable{rest.length ? ` (${rest.length})` : ""}</h2>
      {rest.length === 0 && <p className="muted">Nothing outstanding.</p>}
      {rest.map((p) => <Policy key={p.domain} p={p} />)}
    </>
  );
}
