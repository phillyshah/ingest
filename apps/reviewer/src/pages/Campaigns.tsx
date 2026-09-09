import { useMemo, useState } from "react";
import { Link } from "react-router-dom";
import { DndContext, type DragEndEvent, useDraggable, useDroppable } from "@dnd-kit/core";
import { useQueryClient } from "@tanstack/react-query";
import { post } from "../api/client";
import { createCampaign, previewScope, useCampaigns, type Card as CardT } from "../api/hooks";
import { useAuth, hasRole } from "../auth";
import { Badge, Err, stateKind } from "../components/ui";
import { COLUMNS, columnOf, dragOutcome, funnel, staleness } from "../lib/kanban";
import { useLiveEvents } from "../lib/sse";

function CampaignCard({ c }: { c: CardT }) {
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
    </div>
  );
}

function Column({ k, label, cards }: { k: string; label: string; cards: CardT[] }) {
  const { setNodeRef, isOver } = useDroppable({ id: k });
  return (
    <div ref={setNodeRef} className={`col ${isOver ? "over" : ""}`} data-testid={`col-${k}`}>
      <h3>{label} <span className="muted">{cards.length}</span></h3>
      {cards.map((c) => <CampaignCard key={c.id} c={c} />)}
    </div>
  );
}

function NewCampaign({ onClose }: { onClose: () => void }) {
  const qc = useQueryClient();
  const [title, setTitle] = useState("");
  const [ailment, setAilment] = useState("");
  const [codes, setCodes] = useState("");
  const [priority, setPriority] = useState(100);
  const [output, setOutput] = useState("text_first_phased_plan_templates");
  const [policy, setPolicy] = useState("allowlist_only");
  const [urls, setUrls] = useState("");
  const [limits, setLimits] = useState({ max_sources: 20, max_candidates: 40, max_search_requests: 30, max_runtime_seconds: 7200, max_usd: 10, max_document_bytes: 50_000_000 });
  const [acceptance, setAcceptance] = useState("phases covered, restrictions captured, dose provenance, checkpoints");
  const [media, setMedia] = useState("reference_only");
  const [preview, setPreview] = useState<Record<string, any> | null>(null);
  const [confirmed, setConfirmed] = useState(false);
  const [err, setErr] = useState<unknown>(null);
  const scope = () => ({ ailment_text: ailment || null, codes: codes.split(/[,\s]+/).filter(Boolean), desired_output: output, priority, source_policy: policy,
    limits, acceptance_criteria: acceptance.split(",").map((s) => s.trim()).filter(Boolean), media_policy: media, supplied_source_urls: urls.split(/\s+/).filter(Boolean), scope_confirmed: confirmed });
  const doPreview = async () => { setErr(null); try { setPreview(await previewScope(null, scope())); } catch (e) { setErr(e); } };
  const save = async () => {
    setErr(null);
    try { await createCampaign({ title, scope: scope() }); await qc.invalidateQueries({ queryKey: ["campaigns"] }); onClose(); } catch (e) { setErr(e); }
  };
  return (
    <dialog open>
      <h2>New campaign</h2>
      <p className="small muted">One card = one bounded ingestion request. Limits are ceilings, not targets (spec §21B).</p>
      <div className="row">
        <label className="f">Title<input value={title} onChange={(e) => setTitle(e.target.value)} required /></label>
        <label className="f">Ailment<input value={ailment} onChange={(e) => setAilment(e.target.value)} placeholder="e.g. nonoperative hamstring strain" /></label>
        <label className="f">Diagnostic codes (ICD-10-CM)<input value={codes} onChange={(e) => setCodes(e.target.value)} placeholder="M75.01, S83.411A" /></label>
        <label className="f">Priority<input type="number" value={priority} onChange={(e) => setPriority(Number(e.target.value))} /></label>
        <label className="f">Desired output<select value={output} onChange={(e) => setOutput(e.target.value)}><option value="text_first_phased_plan_templates">text-first phased plan templates</option><option value="exercise_variants_only">exercise variants + evidence only</option></select></label>
        <label className="f">Source policy<select value={policy} onChange={(e) => setPolicy(e.target.value)}><option value="allowlist_only">allowlist only</option><option value="supplied_only">supplied URLs only</option><option value="allowlist_and_supplied">allowlist + supplied</option></select></label>
        <label className="f">Media<select value={media} onChange={(e) => setMedia(e.target.value)}><option value="reference_only">reference only</option><option value="small_graphics">small graphics if permitted</option><option value="none">none</option></select></label>
      </div>
      <label className="f" style={{ marginTop: 8 }}>Supplied source URLs / internal protocols (one per line)<textarea rows={2} value={urls} onChange={(e) => setUrls(e.target.value)} /></label>
      <label className="f" style={{ marginTop: 8 }}>Acceptance coverage (comma separated)<input value={acceptance} onChange={(e) => setAcceptance(e.target.value)} /></label>
      <h3>Run limits</h3>
      <div className="row">
        {(Object.keys(limits) as (keyof typeof limits)[]).map((k) => (
          <label className="f" key={k}>{k.replace(/_/g, " ")}<input type="number" value={limits[k]} onChange={(e) => setLimits({ ...limits, [k]: Number(e.target.value) })} /></label>
        ))}
      </div>
      <div className="row" style={{ marginTop: 10 }}>
        <button onClick={doPreview}>Preview scope</button>
        <label className="row"><input type="checkbox" checked={confirmed} onChange={(e) => setConfirmed(e.target.checked)} /> I confirm the interpreted scope</label>
        <span className="spacer" />
        <button onClick={onClose}>Cancel</button>
        <button className="primary" onClick={save} disabled={!title}>Save as draft</button>
      </div>
      <Err e={err} />
      {preview && (
        <div className="panel" data-testid="scope-preview">
          <h3>Scope preview</h3>
          <p><b>Interpreted:</b> {preview.interpreted_conditions.map((c: any) => `${c.name} (${c.code})`).join(", ") || <span className="muted">none</span>}</p>
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
          <div className="board">{COLUMNS.map((c) => <Column key={c.key} k={c.key} label={c.label} cards={byCol[c.key]} />)}</div>
        </DndContext>
      ) : (
        <table>
          <thead><tr>{(["title", "lifecycle", "control", "priority", "condition_summary", "spend_usd", "last_update"] as (keyof CardT)[]).map((k) => <th key={k} onClick={() => setSort(k)} style={{ cursor: "pointer" }}>{k}{sort === k ? " ▲" : ""}</th>)}</tr></thead>
          <tbody>{sorted.map((c) => <tr key={c.id}><td><Link to={`/campaigns/${c.id}`}>{c.title}</Link></td><td><Badge kind={stateKind(c.lifecycle)}>{c.lifecycle}</Badge></td><td>{c.control}</td><td>{c.priority}</td><td>{c.condition_summary}</td><td>${c.spend_usd.toFixed(2)}</td><td>{new Date(c.last_update).toLocaleString()}</td></tr>)}</tbody>
        </table>
      )}
      {creating && <NewCampaign onClose={() => setCreating(false)} />}
    </>
  );
}
