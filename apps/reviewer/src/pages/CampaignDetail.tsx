import { useEffect, useState } from "react";
import { Link, useParams } from "react-router-dom";
import { get, post } from "../api/client";
import { useQueryClient } from "@tanstack/react-query";
import { patchCampaign, previewScope, useCampaign, useCampaignAction } from "../api/hooks";
import { useAuth, hasRole } from "../auth";
import { Badge, Err, KV, fmt, stateKind } from "../components/ui";
import { funnel } from "../lib/kanban";

const TABS = ["Overview", "Scope & Limits", "Sources & Jobs", "Exercises & Evidence", "Plan Coverage", "Review Queue", "Activity & Costs"] as const;

function useSub<T>(path: string | null, deps: unknown[] = []) {
  const [data, setData] = useState<T | null>(null);
  const [err, setErr] = useState<unknown>(null);
  useEffect(() => { if (!path) return; get<T>(path).then(setData).catch(setErr); }, [path, ...deps]);
  return { data, err };
}

/** The gate that lets a run start (spec §21B).
 *
 * It used to live only inside the New campaign dialog, behind a "Preview scope" button, so a campaign saved
 * straight to draft could never be confirmed afterwards: Start refused, and there was nowhere to say yes. It
 * belongs here, where someone looks when Start refuses.
 *
 * The interpretation is fetched and shown before the button is offered. Confirming without seeing what you are
 * confirming would make the gate decorative.
 */
function ConfirmScope({ id }: { id: string }) {
  const qc = useQueryClient();
  const [preview, setPreview] = useState<Record<string, any> | null>(null);
  const [err, setErr] = useState<unknown>(null);
  const [busy, setBusy] = useState(false);
  useEffect(() => { previewScope(id, undefined).then(setPreview).catch(setErr); }, [id]);
  const confirm = async () => {
    setErr(null);
    setBusy(true);
    try {
      await post(`/ingestion-campaigns/${id}/confirm-scope`);
      await qc.invalidateQueries({ queryKey: ["campaign", id] });
      await qc.invalidateQueries({ queryKey: ["campaigns"] });
    } catch (e) { setErr(e); } finally { setBusy(false); }
  };
  // "scope not confirmed" is the thing this panel resolves; anything else is a real problem to fix first.
  const other = (preview?.blocking_reasons ?? []).filter((r: string) => r !== "scope not confirmed");
  return (
    <div className="panel" data-testid="confirm-scope" style={{ borderLeft: "3px solid #b58900" }}>
      <h2 style={{ marginTop: 0 }}>Before this can start, check the interpretation</h2>
      <p className="small muted">
        This is what the system understood you to be asking for. Confirming records that you agree, and is what
        allows the run to spend against its cap.
      </p>
      <Err e={err} />
      {!preview && !err && <p className="muted">reading the scope…</p>}
      {preview && (
        <>
          <p><b>Interpreted as:</b>{" "}
            {preview.interpreted_conditions?.length
              ? preview.interpreted_conditions.map((x: any) => `${x.name} (${x.code})`).join(", ")
              : <span className="muted">nothing recognised</span>}
          </p>
          {preview.unresolved_text?.length > 0 && <p className="small"><b>Not recognised:</b> {preview.unresolved_text.join("; ")}</p>}
          {preview.suggested_conditions?.length > 0 && (
            <p className="small" data-testid="suggested-conditions">
              <b>Closest matches in the catalog:</b> {preview.suggested_conditions.map((s: any) => `${s.name} (${s.reason})`).join("; ")}
              <span className="muted"> — none of these were applied; edit the scope's ailment text to name one directly if it's right.</span>
            </p>
          )}
          {preview.missing?.length > 0 && (
            <details><summary className="small">Known gaps ({preview.missing.length})</summary>
              <ul className="small">{preview.missing.map((m: string) => <li key={m}>{m}</li>)}</ul>
            </details>
          )}
          <details><summary className="small">Where it will look ({preview.proposed_strategy?.length ?? 0})</summary>
            <ul className="small">{(preview.proposed_strategy ?? []).map((m: string) => <li key={m}>{m}</li>)}</ul>
          </details>
          {other.length > 0 && <div className="error small" style={{ marginTop: 8 }}><b>Fix before confirming:</b> {other.join("; ")}</div>}
          <button className="primary" style={{ marginTop: 10 }} onClick={confirm} disabled={busy || other.length > 0} data-testid="action-confirm_scope">
            {busy ? "Confirming…" : "Yes, this is right — allow it to start"}
          </button>
        </>
      )}
    </div>
  );
}

export default function CampaignDetail() {
  const { id = "" } = useParams();
  const { session } = useAuth();
  const q = useCampaign(id);
  const act = useCampaignAction(id);
  const [tab, setTab] = useState<(typeof TABS)[number]>("Overview");
  const c = q.data;
  const isAdmin = hasRole(session, "source_admin");
  const jobs = useSub<{ items: any[] }>(tab === "Sources & Jobs" ? `/ingestion-jobs?campaign_id=${id}&limit=200` : null, [q.dataUpdatedAt]);
  const items = useSub<{ items: any[] }>(tab === "Sources & Jobs" ? `/ingestion-campaigns/${id}/items` : null, [q.dataUpdatedAt]);
  const ex = useSub<{ variants: any[]; claims: any[] }>(tab === "Exercises & Evidence" ? `/ingestion-campaigns/${id}/exercises` : null, [q.dataUpdatedAt]);
  const queue = useSub<{ items: any[] }>(tab === "Review Queue" ? `/reviews/queue?campaign_id=${id}` : null, [q.dataUpdatedAt]);
  const events = useSub<{ items: any[] }>(tab === "Activity & Costs" ? `/ingestion-campaigns/${id}/events` : null, [q.dataUpdatedAt]);
  const costs = useSub<{ ledger: any[]; by_stage: any[] }>(tab === "Activity & Costs" ? `/ingestion-campaigns/${id}/costs` : null, [q.dataUpdatedAt]);
  const [criterion, setCriterion] = useState("");
  const [reviewerId, setReviewerId] = useState("");
  if (q.error) return <Err e={q.error} />;
  if (!c) return <p className="muted">loading…</p>;
  const run = (action: string) => {
    const reason = action === "cancel" ? window.prompt("Reason for cancelling (results and audit history are retained)") ?? undefined : undefined;
    if (action === "cancel" && reason === undefined) return;
    act.mutate({ action, revision: c.run_revision, reason });
  };
  return (
    <>
      <div className="row">
        <h1 style={{ margin: 0 }}>{c.title}</h1>
        <Badge kind={stateKind(c.lifecycle)}>{c.lifecycle}</Badge>
        {c.control !== "active" && <Badge kind={stateKind(c.control)}>{c.control}</Badge>}
        {c.heartbeat_stale && <Badge kind="warn">worker heartbeat expired</Badge>}
        <span className="spacer" />
        <span className="small muted">last update {fmt(c.last_update)} · run rev {c.run_revision ?? "—"}</span>
        {isAdmin && ["start", "pause", "resume", "cancel", "retry_failed"].filter((a) => c.allowed_actions.includes(a)).map((a) => (
          <button key={a} className={a === "cancel" ? "danger" : a === "start" ? "primary" : ""} onClick={() => run(a)} disabled={act.isPending} data-testid={`action-${a}`}>{a.replace("_", " ")}</button>
        ))}
      </div>
      <Err e={act.error} />
      {c.allowed_actions.includes("confirm_scope") && isAdmin && <ConfirmScope id={id} />}
      {c.blockers.length > 0 && <div className="notice"><b>Blockers:</b> {c.blockers.join(" · ")} {c.next_human_action && <> — <b>next:</b> {c.next_human_action}</>}</div>}
      <div className="tabs">{TABS.map((t) => <button key={t} className={tab === t ? "active" : ""} onClick={() => setTab(t)}>{t}</button>)}</div>

      {tab === "Overview" && (c.pending_publishers ?? []).length > 0 && (
        <div className="panel" data-testid="pending-publishers">
          <h3>Found, but not readable yet</h3>
          <p className="small muted">
            Discovery found these pages, but a page is only read from a publisher whose terms a rights reviewer has accepted.
            Accept the terms on the <Link to="/sources">Sources page</Link>, then press <b>start</b> here — the pages found are kept and picked up by the next run.
            A domain that is not on the list at all is recorded for you and never read; adding one is a change to the curated allowlist.
          </p>
          <table><thead><tr><th>Publisher</th><th>Pages</th><th>Waiting on</th><th>Examples</th></tr></thead>
            <tbody>{(c.pending_publishers ?? []).map((p: any) => <tr key={p.domain}><td>{p.publisher ?? <span className="muted">not on the list</span>}<br /><span className="mono small">{p.domain}</span></td><td>{p.count}</td><td className="small">{p.reason}</td>
              <td className="small">{(p.pages ?? []).map((u: string) => <div key={u}><a href={u} target="_blank" rel="noreferrer">{u.replace(/^https?:\/\/[^/]+/, "")}</a></div>)}</td></tr>)}</tbody></table>
        </div>
      )}
      {tab === "Overview" && (
        <div className="grid2">
          <div className="panel">
            <KV items={[["condition", c.condition_summary], ["scope", c.scope_summary], ["owner", c.owner], ["reviewer", c.reviewer], ["priority", c.priority], ["activity", c.current_activity],
              ["heartbeat", fmt(c.heartbeat_at)], ["active time", `${Math.round(c.active_seconds / 60)} min (computation; reviewer wait time excluded)`], ["spend", `$${c.spend_usd.toFixed(2)} of $${c.cap_usd.toFixed(2)}`],
              ["coverage", Object.entries(c.coverage).map(([k, v]) => `${k}: ${v}`).join(", ") || "no criteria recorded"], ["next human action", c.next_human_action]]} />
          </div>
          <div className="panel">
            <h3>Funnel</h3>
            <div className="row">{funnel(c.counts).map((f) => <div className="stat" key={f.label}><b>{f.value}</b><span>{f.label}</span></div>)}</div>
            <p className="small muted">Denominators are fixed only after discovery closes; no overall percent or ETA is shown until throughput supports it (spec §21D).</p>
            {isAdmin && <div className="row"><input placeholder="reviewer user id" value={reviewerId} onChange={(e) => setReviewerId(e.target.value)} /><button onClick={async () => { await patchCampaign(id, { reviewer_id: reviewerId }); void q.refetch(); }}>Assign reviewer</button></div>}
          </div>
        </div>
      )}
      {tab === "Scope & Limits" && (
        <div className="grid2">
          <div className="panel"><h3>Scope version {String(c.scope.version)}</h3><pre>{JSON.stringify({ ailment: c.scope.ailment_text, codes: c.scope.codes, inclusion: c.scope.inclusion, exclusion: c.scope.exclusion, refinements: c.scope.refinements, output: c.scope.desired_output, source_policy: c.scope.source_policy, media: c.scope.media_policy, supplied: c.scope.supplied_source_urls, acceptance: c.scope.acceptance_criteria, authorized_by: c.scope.authorized_by, authorized_at: c.scope.authorized_at }, null, 2)}</pre>
            {c.warnings.map((w) => <div className="notice" key={w}>{w}</div>)}</div>
          <div className="panel"><h3>Limits (ceilings)</h3><KV items={Object.entries(c.limits).map(([k, v]) => [k, String(v)])} /><h3>Budget ledger state</h3><pre>{JSON.stringify(c.budget, null, 2)}</pre>
            <p className="small muted">Raising limits on an active campaign creates an audited scope revision; the pipeline is never silently restarted (spec §21E).</p></div>
        </div>
      )}
      {tab === "Sources & Jobs" && (
        <>
          <Err e={jobs.err ?? items.err} />
          <div className="panel"><h3>Sources</h3><table><thead><tr><th>Table</th><th>Item</th><th>Disposition</th><th>Detail</th></tr></thead>
            <tbody>{(items.data?.items ?? []).map((i) => <tr key={i.id}><td>{i.item_table}</td><td className="mono">{i.item_id}</td><td><Badge kind={stateKind(i.disposition)}>{i.disposition}</Badge></td><td className="small">{JSON.stringify(i.detail)}</td></tr>)}</tbody></table></div>
          <div className="panel"><h3>Jobs</h3><table><thead><tr><th>Stage</th><th>State</th><th>Attempt</th><th>Source version</th><th>Error</th><th>Cost</th><th>Retry</th></tr></thead>
            <tbody>{(jobs.data?.items ?? []).map((j) => <tr key={j.id}><td>{j.stage}</td><td><Badge kind={stateKind(j.state)}>{j.state}</Badge></td><td>{j.attempts}/{j.max_attempts}</td><td className="mono">{j.source_version_id?.slice(0, 8)} <span className="muted">{j.pipeline_state}</span></td><td className="small">{j.error_class} {j.last_error?.slice(0, 120)}</td><td>${Number(j.cost_usd).toFixed(4)}</td>
              <td>{j.retry_eligible && isAdmin ? <button onClick={async () => { await post(`/ingestion-jobs/${j.id}/retry`); void q.refetch(); }}>retry</button> : j.state === "failed" ? <span className="muted small">permanent</span> : null}</td></tr>)}</tbody></table></div>
        </>
      )}
      {tab === "Exercises & Evidence" && (
        <>
          <Err e={ex.err} />
          <div className="panel"><h3>Variants ({ex.data?.variants.length ?? 0})</h3><table><thead><tr><th>Name</th><th>Assistance</th><th>State</th><th>Duplicate of</th><th>Source</th><th>Flags</th></tr></thead>
            <tbody>{(ex.data?.variants ?? []).map((v) => <tr key={v.id}><td><Link to={`/reviews/exercise_variant_version/${v.id}`}>{v.name}</Link> <span className="muted small">v{v.version}</span></td><td>{v.assistance}</td><td><Badge kind={stateKind(v.approval_state)}>{v.approval_state}</Badge></td><td className="mono small">{v.duplicate_of_entity_id?.slice(0, 8) ?? "—"}</td><td className="small">{v.source_reference?.publisher} · {JSON.stringify(v.source_reference?.locator)}</td><td className="small">{(v.extraction_warnings ?? []).join("; ")}</td></tr>)}</tbody></table></div>
          <div className="panel"><h3>Evidence claims ({ex.data?.claims.length ?? 0})</h3><table><thead><tr><th>Type</th><th>Paraphrase</th><th>Locator</th><th>Flags</th></tr></thead>
            <tbody>{(ex.data?.claims ?? []).map((cl) => <tr key={cl.id}><td>{cl.claim_type}</td><td>{cl.paraphrase}</td><td className="mono small">{JSON.stringify(cl.locator)}</td><td>{(cl.ambiguity_flags ?? []).map((f: string) => <Badge key={f} kind="warn">{f}</Badge>)}</td></tr>)}</tbody></table></div>
        </>
      )}
      {tab === "Plan Coverage" && (
        <div className="panel">
          <p className="small muted">Satisfied / required acceptance criteria, with unknown items visible. Reaching an exercise cap never marks a campaign complete (spec §21D/G).</p>
          <table><thead><tr><th>Criterion</th><th>State</th><th>Evidence</th><th>Reviewer</th><th>Updated</th></tr></thead>
            <tbody>{c.coverage_checks.map((k: any) => <tr key={k.id}><td>{k.criterion}</td><td><Badge kind={stateKind(k.state)}>{k.state}</Badge></td><td className="small">{JSON.stringify(k.evidence)}</td><td className="mono small">{k.reviewer_id?.slice(0, 8)}</td><td>{fmt(k.updated_at)}</td></tr>)}
              {((c.scope.acceptance_criteria as string[]) ?? []).filter((a) => !c.coverage_checks.some((k: any) => k.criterion === a)).map((a) => <tr key={a}><td>{a}</td><td><Badge>unknown</Badge></td><td colSpan={3} className="muted small">not yet assessed</td></tr>)}</tbody></table>
          {hasRole(session, "pt", "clinical_lead") && <div className="row" style={{ marginTop: 8 }}><input placeholder="criterion" value={criterion} onChange={(e) => setCriterion(e.target.value)} />
            {["met", "unmet", "not_applicable"].map((s) => <button key={s} onClick={async () => { await post(`/ingestion-campaigns/${id}/coverage`, { criterion, state: s }); void q.refetch(); }}>{s}</button>)}</div>}
        </div>
      )}
      {tab === "Review Queue" && (
        <div className="panel"><Err e={queue.err} /><table><thead><tr><th>Item</th><th>Table</th><th>Created</th><th>Flags</th><th>Duplicate?</th></tr></thead>
          <tbody>{(queue.data?.items ?? []).map((i) => <tr key={i.version_id}><td><Link to={`/reviews/${i.entity_table}/${i.version_id}`}>{i.name ?? i.version_id}</Link></td><td>{i.entity_table}</td><td>{fmt(i.created_at)}</td><td className="small">{(i.flags ?? []).join("; ")}</td><td>{i.duplicate_of_entity_id ? <Badge kind="warn">proposed duplicate</Badge> : "—"}</td></tr>)}</tbody></table></div>
      )}
      {tab === "Activity & Costs" && (
        <div className="grid2">
          <div className="panel"><h3>Events</h3><table><thead><tr><th>At</th><th>Event</th><th>Detail</th></tr></thead><tbody>{(events.data?.items ?? []).slice().reverse().map((e) => <tr key={e.id}><td className="small">{fmt(e.created_at)}</td><td>{e.event}</td><td className="small mono">{JSON.stringify(e.detail).slice(0, 200)}</td></tr>)}</tbody></table></div>
          <div className="panel"><h3>Costs</h3><table><thead><tr><th>Stage</th><th>State</th><th>Jobs</th><th>USD</th><th>ms</th></tr></thead><tbody>{(costs.data?.by_stage ?? []).map((s, i) => <tr key={i}><td>{s.stage}</td><td>{s.state}</td><td>{s.n}</td><td>{Number(s.usd).toFixed(4)}</td><td>{s.ms}</td></tr>)}</tbody></table>
            <h3>Budget ledger</h3><table><thead><tr><th>Kind</th><th>USD</th><th>Requests</th><th>Bytes</th><th>At</th></tr></thead><tbody>{(costs.data?.ledger ?? []).map((l) => <tr key={l.id}><td>{l.kind}</td><td>{Number(l.usd).toFixed(4)}</td><td>{l.search_requests}</td><td>{l.document_bytes}</td><td className="small">{fmt(l.created_at)}</td></tr>)}</tbody></table></div>
        </div>
      )}
    </>
  );
}
