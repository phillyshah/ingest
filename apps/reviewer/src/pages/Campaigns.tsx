import { useMemo, useState } from "react";
import { Link } from "react-router-dom";
import { DndContext, type DragEndEvent, useDraggable, useDroppable } from "@dnd-kit/core";
import { useQueryClient } from "@tanstack/react-query";
import { del, post } from "../api/client";
import { createCampaign, previewScope, useCampaigns, type Card as CardT } from "../api/hooks";
import { useAuth, hasRole } from "../auth";
import { Badge, Err, stateKind } from "../components/ui";
import { COLUMNS, columnOf, dragOutcome, funnel, staleness } from "../lib/kanban";
import { useLiveEvents } from "../lib/sse";

function CampaignCard({ c, onDelete }: { c: CardT; onDelete?: (c: CardT) => void }) {
  const { attributes, listeners, setNodeRef, transform } = useDraggable({ id: c.id, data: c });
  const style = transform ? { transform: `translate(${transform.x}px, ${transform.y}px)` } : undefined;
  return (
    <div ref={setNodeRef} style={style} className="card" {...listeners} {...attributes} data-testid={`card-${c.id}`}>
      <div className="t"><Link to={`/campaigns/${c.id}`}>{c.title}</Link></div>
      <div>{c.condition_summary ?? <span className="muted">no condition resolved</span>}</div>
      <div className="muted">{c.owner ?? "unowned"} · reviewer {c.reviewer ?? "—"} · prio {c.priority}</div>
      <div style={{ margin: "4px 0" }}>
        {c.control !== "active" && <Badge kind={stateKind(c.control)}>{c.control}</Badge>}
        {c.lifecycle === "closed_incomplete" && <Badge kind="bad">closed incomplete</Badge>}
        {c.heartbeat_stale && <Badge kind="warn">heartbeat expired</Badge>}
      </div>
      <div className="funnel">{funnel(c.counts).map((f) => <span key={f.label}><b>{f.value}</b> {f.label}</span>)}</div>
      <div className="muted">spend ${c.spend_usd.toFixed(2)} / cap ${c.cap_usd.toFixed(2)} · active {Math.round(c.active_seconds / 60)}m · {c.current_activity ?? "idle"}</div>
      {c.blockers.length > 0 && <div style={{ color: "var(--bad)" }}>{c.blockers[0]}</div>}
      {c.next_human_action && <div><Badge kind="info">next: {c.next_human_action}</Badge></div>}
      {onDelete && (
        <div style={{ marginTop: 6 }}>
          {/* Not inside the drag listeners above: pointer-down on a draggable would start a drag instead. */}
          <button
            className="small"
            onPointerDown={(e) => e.stopPropagation()}
            onClick={(e) => { e.stopPropagation(); onDelete(c); }}
            title="Remove this campaign from the board"
          >Delete</button>
        </div>
      )}
    </div>
  );
}

function Column({ k, label, cards, onDelete }: { k: string; label: string; cards: CardT[]; onDelete?: (c: CardT) => void }) {
  const { setNodeRef, isOver } = useDroppable({ id: k });
  return (
    <div ref={setNodeRef} className={`col ${isOver ? "over" : ""}`} data-testid={`col-${k}`}>
      <h3>{label} <span className="muted">{cards.length}</span></h3>
      {cards.map((c) => <CampaignCard key={c.id} c={c} onDelete={onDelete} />)}
    </div>
  );
}

function NewCampaign({ onClose }: { onClose: () => void }) {
  const qc = useQueryClient();
  const [title, setTitle] = useState("");
  const [ailment, setAilment] = useState("");
  const [codes, setCodes] = useState("");
  // Campaigns sort by `order by priority, created_at`, so a LOWER number runs sooner.
  const [priority, setPriority] = useState(100);
  const [output, setOutput] = useState("text_first_phased_plan_templates");
  const [urls, setUrls] = useState("");
  const [limits, setLimits] = useState({ max_sources: 20, max_candidates: 40, max_search_requests: 30, max_runtime_seconds: 7200, max_usd: 10, max_document_bytes: 50_000_000 });
  const [acceptance, setAcceptance] = useState("phases covered, restrictions captured, dose provenance, checkpoints");
  const [media, setMedia] = useState("reference_only");
  const [preview, setPreview] = useState<Record<string, any> | null>(null);
  const [confirmed, setConfirmed] = useState(false);
  const [err, setErr] = useState<unknown>(null);
  // Conditions accepted from `suggested_conditions` (the fallback for ailment text too general/colloquial for the
  // exact match) — never populated by the preview itself, only by clicking "Use this" below.
  const [acceptedConditionIds, setAcceptedConditionIds] = useState<string[]>([]);
  const urlList = urls.split(/\s+/).filter(Boolean);
  const scope = () => ({
    ailment_text: ailment || null,
    codes: codes.split(/[,\s]+/).filter(Boolean),
    desired_output: output,
    priority,
    // Derived rather than asked: supplying documents obviously means "use them too". There is no case where a
    // person wants to paste URLs and then have them ignored.
    source_policy: urlList.length > 0 ? "allowlist_and_supplied" : "allowlist_only",
    limits,
    acceptance_criteria: acceptance.split(",").map((s) => s.trim()).filter(Boolean),
    media_policy: media,
    supplied_source_urls: urlList,
    scope_confirmed: confirmed,
    additional_condition_ids: acceptedConditionIds,
  });
  const doPreview = async () => { setErr(null); try { setPreview(await previewScope(null, scope())); } catch (e) { setErr(e); } };
  const acceptSuggestion = async (id: string) => {
    setAcceptedConditionIds((prev) => [...prev, id]);
    setErr(null);
    try { setPreview(await previewScope(null, { ...scope(), additional_condition_ids: [...acceptedConditionIds, id] })); } catch (e) { setErr(e); }
  };
  const save = async () => {
    setErr(null);
    try { await createCampaign({ title, scope: scope() }); await qc.invalidateQueries({ queryKey: ["campaigns"] }); onClose(); } catch (e) { setErr(e); }
  };
  return (
    <dialog open>
      <h2>New campaign</h2>
      <p className="small muted">Describe what you want covered. Everything else has a sensible default.</p>
      <div className="row">
        <label className="f">Title<input value={title} onChange={(e) => setTitle(e.target.value)} required placeholder="MCL sprain" /></label>
        <label className="f">What should it cover?<input value={ailment} onChange={(e) => setAilment(e.target.value)} placeholder="e.g. grade 1 MCL sprain, nonoperative" /></label>
        <label className="f">Priority
          <select value={priority} onChange={(e) => setPriority(Number(e.target.value))}>
            <option value={50}>High</option>
            <option value={100}>Medium</option>
            <option value={200}>Low</option>
          </select>
        </label>
      </div>
      <label className="f" style={{ marginTop: 8 }}>Diagnostic codes <span className="muted small">— optional, ICD-10-CM</span>
        <input value={codes} onChange={(e) => setCodes(e.target.value)} placeholder="M75.01, S83.411A" />
      </label>

      {/* Everything below is defaulted. It stays reachable because these are real ceilings on spend and scope,
          but nobody should have to answer six questions to create a draft. */}
      <details style={{ marginTop: 12 }}>
        <summary className="small">Advanced settings</summary>
        <label className="f" style={{ marginTop: 8 }}>Specific documents to use <span className="muted small">— optional, one URL per line. Leave empty and the engine finds its own sources.</span>
          <textarea rows={2} value={urls} onChange={(e) => setUrls(e.target.value)} />
        </label>
        <div className="row" style={{ marginTop: 8 }}>
          <label className="f">Output<select value={output} onChange={(e) => setOutput(e.target.value)}><option value="text_first_phased_plan_templates">Phased plan templates</option><option value="exercise_variants_only">Exercises and evidence only</option></select></label>
          <label className="f">Images<select value={media} onChange={(e) => setMedia(e.target.value)}><option value="reference_only">Reference only</option><option value="small_graphics">Include if licensed</option><option value="none">None</option></select></label>
        </div>
        <label className="f" style={{ marginTop: 8 }}>What counts as covered <span className="muted small">— comma separated</span>
          <input value={acceptance} onChange={(e) => setAcceptance(e.target.value)} />
        </label>
        <h3>Ceilings</h3>
        <p className="small muted">Hard stops, not targets. The run halts when it reaches any of them.</p>
        <div className="row">
          <label className="f">Spend cap (US$)<input type="number" value={limits.max_usd} onChange={(e) => setLimits({ ...limits, max_usd: Number(e.target.value) })} /></label>
          <label className="f">Max sources read<input type="number" value={limits.max_sources} onChange={(e) => setLimits({ ...limits, max_sources: Number(e.target.value) })} /></label>
          <label className="f">Max exercises proposed<input type="number" value={limits.max_candidates} onChange={(e) => setLimits({ ...limits, max_candidates: Number(e.target.value) })} /></label>
          <label className="f">Max searches<input type="number" value={limits.max_search_requests} onChange={(e) => setLimits({ ...limits, max_search_requests: Number(e.target.value) })} /></label>
          <label className="f">Time limit (minutes)<input type="number" value={Math.round(limits.max_runtime_seconds / 60)} onChange={(e) => setLimits({ ...limits, max_runtime_seconds: Number(e.target.value) * 60 })} /></label>
          <label className="f">Max document size (MB)<input type="number" value={Math.round(limits.max_document_bytes / 1_000_000)} onChange={(e) => setLimits({ ...limits, max_document_bytes: Number(e.target.value) * 1_000_000 })} /></label>
        </div>
      </details>

      <div className="row" style={{ marginTop: 10 }}>
        <button onClick={doPreview}>Preview scope</button>
        <span className="spacer" />
        <button onClick={onClose}>Cancel</button>
        <button className="primary" onClick={save} disabled={!title}>Save as draft</button>
      </div>
      {/* The confirmation gate is required before a run may start (spec §21B), but it is meaningless until there
          is an interpretation to confirm — so it appears with the preview rather than above it. */}
      {preview && (
        <label className="row" style={{ marginTop: 8 }}>
          <input type="checkbox" checked={confirmed} onChange={(e) => setConfirmed(e.target.checked)} />
          &nbsp;The interpretation below is right — allow this campaign to start
        </label>
      )}
      <Err e={err} />
      {preview && (
        <div className="panel" data-testid="scope-preview">
          <h3>Scope preview</h3>
          <p><b>Interpreted:</b> {preview.interpreted_conditions.map((c: any) => `${c.name} (${c.code})`).join(", ") || <span className="muted">none</span>}</p>
          {preview.suggested_conditions?.length > 0 && (
            <div className="notice small" data-testid="suggested-conditions">
              <b>Did you mean:</b>
              <ul>{preview.suggested_conditions.map((s: any) => (
                <li key={s.id}>
                  {s.name} ({s.code}) <span className="muted">— {s.reason}, confidence {s.confidence}</span>{" "}
                  <button className="small" onClick={() => acceptSuggestion(s.id)}>Use this</button>
                </li>
              ))}</ul>
              <span className="muted">Nothing here is applied until you click "Use this" — what you typed did not match anything in the catalog on its own.</span>
            </div>
          )}
          {preview.resolved_codes?.length > 0 && <table><thead><tr><th>Code</th><th>Descriptor</th><th>Release</th><th>Laterality</th><th>Billable</th><th>Note</th></tr></thead>
            <tbody>{preview.resolved_codes.map((r: any) => <tr key={r.code}><td className="mono">{r.code}</td><td>{r.descriptor ?? <Badge kind="bad">unresolved</Badge>}</td><td>{r.release_label}</td><td>{r.laterality}</td><td>{String(r.billable)}</td><td className="muted">{r.note}</td></tr>)}</tbody></table>}
          <p><b>Existing coverage:</b> <span className="mono">{JSON.stringify(preview.existing_coverage)}</span></p>
          <p><b>Missing:</b> {preview.missing.length ? <ul>{preview.missing.map((m: string) => <li key={m}>{m}</li>)}</ul> : "none recorded"}</p>
          <p><b>Strategy:</b> <ul>{preview.proposed_strategy.map((m: string) => <li key={m}>{m}</li>)}</ul></p>
          <p><b>Review requirements:</b> {preview.clinical_review_requirements.join("; ")}</p>
          <p>{preview.can_start ? <Badge kind="ok">can start</Badge> : <Badge kind="warn">cannot start: {preview.blocking_reasons.join("; ")}</Badge>}</p>
        </div>
      )}
    </dialog>
  );
}

// Header labels, because the raw column names are database fields, not English.
const COLS: [keyof CardT, string][] = [
  ["title", "Title"],
  ["lifecycle", "State"],
  ["control", "Control"],
  ["priority", "Priority"],
  ["condition_summary", "Covers"],
  ["spend_usd", "Spent"],
  ["last_update", "Last update"],
];

export default function Campaigns() {
  const { session } = useAuth();
  const qc = useQueryClient();
  const [showArchived, setShowArchived] = useState(false);
  const [view, setView] = useState<"board" | "table">("board");
  const [sort, setSort] = useState<keyof CardT>("priority");
  const [filter, setFilter] = useState("");
  const [creating, setCreating] = useState(false);
  const [msg, setMsg] = useState<string | null>(null);
  const q = useCampaigns(showArchived ? "?include_archived=true" : "");
  const live = useLiveEvents(() => { void qc.invalidateQueries({ queryKey: ["campaigns"] }); });
  const cards = useMemo(() => (q.data?.items ?? []).filter((c) => !filter || c.title.toLowerCase().includes(filter.toLowerCase()) || (c.condition_summary ?? "").toLowerCase().includes(filter.toLowerCase())), [q.data, filter]);
  const byCol = useMemo(() => Object.fromEntries(COLUMNS.map((c) => [c.key, cards.filter((x) => columnOf(x) === c.key)])), [cards]);
  const onDragEnd = async (e: DragEndEvent) => {
    const card = e.active.data.current as CardT | undefined;
    const target = e.over?.id as string | undefined;
    if (!card || !target) return;
    const out = dragOutcome(card, target);
    if (out.kind === "refused") { setMsg(out.reason); return; }
    try {
      if (out.kind === "start") await post(`/ingestion-campaigns/${card.id}/transition`, { to: "queued" });
      setMsg(null);
    } catch (ex) { setMsg((ex as Error).message); }
    void qc.invalidateQueries({ queryKey: ["campaigns"] });
  };
  const sorted = [...cards].sort((a, b) => (a[sort] as any) > (b[sort] as any) ? 1 : -1);
  const canDelete = hasRole(session, "source_admin");
  const doDelete = async (c: CardT) => {
    if (!window.confirm(`Delete "${c.title}"?\n\nIt disappears from the board. Its history is kept, because the audit trail of what was ingested cannot be erased.`)) return;
    try {
      await del(`/ingestion-campaigns/${c.id}`);
      setMsg(null);
    } catch (ex) {
      // A campaign that has already run is refused rather than hidden — say so instead of failing silently.
      setMsg((ex as Error).message);
    }
    void qc.invalidateQueries({ queryKey: ["campaigns"] });
  };
  return (
    <>
      <div className="row" style={{ marginBottom: 10 }}>
        <h1 style={{ margin: 0 }}>Campaigns</h1>
        <span className="badge" data-testid="live-status">{live.connected ? "live" : "offline"} · {staleness(live.lastAt ?? q.data?.server_time ?? null, false, live.connected || !!q.data)}</span>
        <span className="spacer" />
        <input placeholder="filter" value={filter} onChange={(e) => setFilter(e.target.value)} />
        <label><input type="checkbox" checked={showArchived} onChange={(e) => setShowArchived(e.target.checked)} /> archive</label>
        <button onClick={() => setView(view === "board" ? "table" : "board")}>{view === "board" ? "Table view" : "Board view"}</button>
        {hasRole(session, "source_admin") && <button className="primary" onClick={() => setCreating(true)} data-testid="new-campaign">New campaign</button>}
      </div>
      <Err e={q.error} />
      {msg && <div className="notice" data-testid="drag-message">{msg}</div>}
      {view === "board" ? (
        <DndContext onDragEnd={onDragEnd}>
          <div className="board">{COLUMNS.map((c) => <Column key={c.key} k={c.key} label={c.label} cards={byCol[c.key]} onDelete={canDelete ? doDelete : undefined} />)}</div>
        </DndContext>
      ) : (
        <table>
          <thead><tr>{COLS.map(([k, label]) => <th key={k} onClick={() => setSort(k)} style={{ cursor: "pointer" }}>{label}{sort === k ? " ▲" : ""}</th>)}<th /></tr></thead>
          <tbody>{sorted.map((c) => <tr key={c.id}><td><Link to={`/campaigns/${c.id}`}>{c.title}</Link></td><td><Badge kind={stateKind(c.lifecycle)}>{c.lifecycle}</Badge></td><td>{c.control}</td><td>{c.priority}</td><td>{c.condition_summary}</td><td>${c.spend_usd.toFixed(2)}</td><td>{new Date(c.last_update).toLocaleString()}</td><td>{canDelete && <button className="small" onClick={() => doDelete(c)}>Delete</button>}</td></tr>)}</tbody>
        </table>
      )}
      {creating && <NewCampaign onClose={() => setCreating(false)} />}
    </>
  );
}
