import { useState } from "react";
import { Link } from "react-router-dom";
import { upload } from "../api/client";
import { useCaptureTerms, useSourcePolicies, useSourcePolicyDecision, useSources, type PolicyStatus, type SourcePolicy, type SourceRow } from "../api/hooks";
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

const STATUS: Record<PolicyStatus, { label: string; kind: "ok" | "warn" | "bad" | "info" | "hold" }> = {
  readable: { label: "accepted · readable", kind: "ok" },
  awaiting_acceptance: { label: "waiting for you to accept", kind: "info" },
  terms_unread: { label: "their terms could not be read", kind: "warn" },
  accepted_but_unusable: { label: "accepted, but the licence forbids reading", kind: "hold" },
  rejected: { label: "rejected", kind: "bad" },
};

function Policy({ p }: { p: SourcePolicy }) {
  const { session } = useAuth();
  const decide = useSourcePolicyDecision();
  const recapture = useCaptureTerms();
  const canDecide = hasRole(session, "rights_reviewer", "clinical_lead");
  const canRead = hasRole(session, "rights_reviewer", "clinical_lead", "source_admin");
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
        <Badge kind={STATUS[p.status].kind}>{STATUS[p.status].label}</Badge>
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

      <Err e={recapture.error} />
      <div className="row" style={{ marginTop: 8 }}>
        <button className="small" onClick={() => setOpen(!open)}>
          {open ? "Hide" : "What this licence allows"}
        </button>
        {/* The terms have to be on record before anyone can accept them, and the only way to get them there used
            to be a GitHub workflow. That is why a publisher whose page was momentarily unreachable stayed stuck. */}
        {canRead && !p.effective && p.status !== "rejected" && (
          <button className="small" disabled={recapture.isPending} onClick={() => recapture.mutate(p.domain)}>
            {recapture.isPending ? "Reading…" : p.terms_fetched_at ? "Read their terms again" : "Read their terms now"}
          </button>
        )}
      </div>

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

/** Upload a PDF of your own — a printed protocol sheet, a handout — and run it through the same pipeline as
 * anything fetched from the web: parsed, extracted, matched against the catalog, queued for PT review. No
 * publisher policy applies, because nothing is being fetched from someone else's site; the operator supplied the
 * file, so it is treated as owned content, same as the demo fixtures always have been. If the PDF has exercise
 * photos embedded in it, those are pulled out and attached to the one exercise on the same page automatically —
 * a page with more than one candidate exercise is left for a PT to attach manually instead of guessing. */
function UploadPdf() {
  const { session } = useAuth();
  const [file, setFile] = useState<File | null>(null);
  const [title, setTitle] = useState("");
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<unknown>(null);
  const [done, setDone] = useState<{ job_id: string } | null>(null);
  if (!hasRole(session, "source_admin")) return null;

  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!file) return;
    setErr(null);
    setBusy(true);
    setDone(null);
    try {
      const form = new FormData();
      form.append("file", file);
      form.append("title", title || file.name);
      const out = await upload<{ job_id: string }>("/sources/upload", form);
      setDone(out);
      setFile(null);
      setTitle("");
    } catch (ex) {
      setErr(ex);
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="panel" style={{ marginBottom: 16 }}>
      <h2 style={{ marginTop: 0 }}>Upload a PDF</h2>
      <p className="small muted">
        Your own protocol sheets and handouts — not something to allowlist, since nothing is fetched from anyone
        else's site. It runs through the same pipeline as any other source and lands in the review queue.
      </p>
      <form onSubmit={submit} className="row" style={{ alignItems: "flex-end", flexWrap: "wrap" }}>
        <label className="f">
          PDF file
          <input type="file" accept="application/pdf" onChange={(e) => setFile(e.target.files?.[0] ?? null)} required />
        </label>
        <label className="f">
          Title <span className="muted small">— optional, defaults to the filename</span>
          <input value={title} onChange={(e) => setTitle(e.target.value)} placeholder={file?.name} />
        </label>
        <button className="primary" type="submit" disabled={busy || !file}>{busy ? "Uploading…" : "Upload"}</button>
      </form>
      <Err e={err} />
      {done && (
        <div className="notice small" style={{ marginTop: 8 }}>
          Uploaded. It is being parsed now — check the <Link to="/reviews">review queue</Link> shortly for anything
          it found.
        </div>
      )}
    </div>
  );
}

export default function Sources() {
  const q = useSourcePolicies();
  const { session } = useAuth();
  const [tab, setTab] = useState<"files" | "publishers">("files");
  if (q.error) return <Err e={q.error} />;
  if (!q.data) return <p className="muted">loading…</p>;
  const by = (s: PolicyStatus) => q.data!.items.filter((p) => p.status === s);
  const counts = q.data.by_status ?? {};

  return (
    <>
      <h1>Sources</h1>
      <div className="tabs">
        <button className={tab === "files" ? "active" : ""} onClick={() => setTab("files")} data-testid="tab-files">Files &amp; pages</button>
        <button className={tab === "publishers" ? "active" : ""} onClick={() => setTab("publishers")} data-testid="tab-publishers">Publishers ({q.data.readable} of {q.data.total} readable)</button>
      </div>

      {tab === "files" && (
        <>
          <UploadPdf />
          <Files />
        </>
      )}

      {tab === "publishers" && <>
      <h2>The curated allowlist</h2>
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

      {/* One line per state, so "why can I only accept five of them?" is answered before it is asked. */}
      <div className="row" style={{ flexWrap: "wrap", gap: 8, marginBottom: 12 }} data-testid="policy-status-counts">
        {(Object.keys(STATUS) as PolicyStatus[]).map((s) => (
          <div className="stat" key={s}><b>{counts[s] ?? 0}</b><span>{STATUS[s].label}</span></div>
        ))}
      </div>

      <div className="notice">
        <b>{q.data.readable} of {q.data.total} publishers can be read.</b>{" "}
        A publisher becomes readable when its licence terms have been fetched from its own site <i>and</i> a rights
        reviewer has accepted that exact text. If a publisher rewrites its terms, the acceptance lapses by itself
        and the publisher stops being readable until someone reads the new wording.
      </div>

      <Group
        title="Waiting for you to accept"
        blurb="Their terms have been read and are on screen below. Accepting one makes the publisher readable straight away."
        items={by("awaiting_acceptance")}
        empty="Nothing waiting on you."
      />
      <Group
        title="Accepted and readable"
        blurb="Campaigns may read pages from these publishers."
        items={by("readable")}
        empty="None yet."
      />
      <Group
        title="Their terms could not be read"
        blurb="These cannot be accepted yet — not because of a decision, but because the text nobody has read cannot be signed. Press “Read their terms now” to try again; if the site refuses automated clients or the URL is wrong, that is a change to the publisher list rather than something to fix here."
        items={by("terms_unread")}
        empty="None."
      />
      <Group
        title="Accepted, but the licence forbids reading"
        blurb="Someone read these terms and accepted them, and the licence still does not permit reading. Nothing further to do: using them needs a separate agreement with the publisher."
        items={by("accepted_but_unusable")}
        empty="None."
      />
      <Group title="Rejected" blurb="Decided against, with the reason recorded." items={by("rejected")} empty="None." />
      </>}
    </>
  );
}

function Group({ title, blurb, items, empty }: { title: string; blurb: string; items: SourcePolicy[]; empty: string }) {
  return (
    <section style={{ marginTop: 18 }}>
      <h2 style={{ marginBottom: 2 }}>{title} {items.length > 0 && <span className="muted">({items.length})</span>}</h2>
      <p className="small muted" style={{ marginTop: 0 }}>{blurb}</p>
      {items.length === 0 ? <p className="muted small">{empty}</p> : items.map((p) => <Policy key={p.domain} p={p} />)}
    </section>
  );
}

// How far a document got, in words. The pipeline state names are internal; the operator wants to know whether
// the thing they uploaded yesterday produced exercises, is still going, or got stuck.
function progress(s: SourceRow): { text: string; kind: "ok" | "bad" | "warn" | "info" | "hold" } {
  if (s.allowlist_state === "denied") return { text: "refused", kind: "bad" };
  if (s.last_problem && s.jobs_active === 0 && s.variants === 0) return { text: "stuck", kind: "bad" };
  if (s.jobs_active > 0) return { text: "being read", kind: "info" };
  const st = s.latest_pipeline_state;
  if (!st || st === "discovered") return { text: s.allowlist_state === "pending" ? "waiting on publisher terms" : "queued", kind: "warn" };
  if (st === "rights_hold") return { text: "held: rights", kind: "warn" };
  if (st === "parse_failed" || st === "extraction_failed") return { text: "could not be read", kind: "bad" };
  if (st === "withdrawn" || st === "superseded" || st === "rejected") return { text: st, kind: "hold" };
  if (s.variants === 0) return { text: "read, no exercises found", kind: "warn" };
  if (s.awaiting_review > 0) return { text: `${s.awaiting_review} awaiting review`, kind: "info" };
  return { text: "reviewed", kind: "ok" };
}

function kindOf(s: SourceRow): string {
  if (s.source_type === "clinician_upload") return "uploaded PDF";
  if (s.canonical_url.startsWith("file:")) return "owned file";
  return s.source_type === "pdf" ? "web PDF" : "web page";
}

function where(s: SourceRow): string {
  if (s.canonical_url.startsWith("file:")) return s.uploaded_by ? `uploaded by ${s.uploaded_by}` : "local file";
  try { return new URL(s.canonical_url).hostname; } catch { return s.canonical_url; }
}

/** Everything the system has been given or has found, and how far each one got. */
function Files() {
  const q = useSources();
  const [showFound, setShowFound] = useState(false);
  if (q.error) return <Err e={q.error} />;
  if (!q.data) return <p className="muted">loading…</p>;
  const all = q.data.items;
  // What someone deliberately gave the system comes first; pages discovery found but has not read yet are noise
  // until they are, so they sit behind a toggle.
  const given = all.filter((s) => s.source_type === "clinician_upload" || s.allowlist_state === "approved" || (s.latest_pipeline_state && s.latest_pipeline_state !== "discovered"));
  const found = all.filter((s) => !given.includes(s));
  const rows = showFound ? all : given;
  const totals = given.reduce((t, s) => ({ variants: t.variants + s.variants, awaiting: t.awaiting + s.awaiting_review, approved: t.approved + s.approved }), { variants: 0, awaiting: 0, approved: 0 });
  return (
    <div className="panel" data-testid="files">
      <h2 style={{ marginTop: 0 }}>What has been ingested</h2>
      <p className="small muted">
        {given.length} document{given.length === 1 ? "" : "s"} read or being read · {totals.variants} exercise{totals.variants === 1 ? "" : "s"} extracted · {totals.awaiting} awaiting review · {totals.approved} approved.
        {found.length > 0 && <> {" "}<button className="small" onClick={() => setShowFound(!showFound)}>{showFound ? "Hide" : "Show"} {found.length} page{found.length === 1 ? "" : "s"} found by campaigns but not read yet</button></>}
      </p>
      {rows.length === 0 && <p className="muted">Nothing yet. Upload a PDF above, or start a campaign.</p>}
      {rows.length > 0 && (
        <table>
          <thead><tr><th>Document</th><th>Kind</th><th>From</th><th>Added</th><th>Status</th><th>Exercises</th><th>Photos</th></tr></thead>
          <tbody>
            {rows.map((s) => {
              const p = progress(s);
              return (
                <tr key={s.id}>
                  <td>
                    {s.title || s.canonical_url.split("/").pop() || s.canonical_url}
                    {!s.canonical_url.startsWith("file:") && <><br /><a className="small" href={s.canonical_url} target="_blank" rel="noreferrer">{s.canonical_url.replace(/^https?:\/\//, "").slice(0, 80)}</a></>}
                    {s.last_problem && <div className="small muted">{s.last_problem}</div>}
                  </td>
                  <td className="small">{kindOf(s)}</td>
                  <td className="small">{where(s)}</td>
                  <td className="small">{fmt(s.created_at)}</td>
                  <td><Badge kind={p.kind}>{p.text}</Badge></td>
                  <td>
                    {s.variants}
                    {s.awaiting_review > 0 && <> · <Link to="/reviews" className="small">{s.awaiting_review} to review</Link></>}
                    {s.approved > 0 && <span className="small muted"> · {s.approved} approved</span>}
                  </td>
                  <td>{s.photos || "—"}</td>
                </tr>
              );
            })}
          </tbody>
        </table>
      )}
    </div>
  );
}
