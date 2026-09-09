import { useState } from "react";
import { post, patch } from "../api/client";
import { useAuth, hasRole } from "../auth";
import { Badge, Err, stateKind } from "../components/ui";

type Status = "known" | "unknown" | "not_assessed" | "not_applicable";
interface Field { status: Status; value?: unknown; unit?: string }

// Intake fields shown in the form (spec §8). Values are entered as JSON-ish text; tri-state status is explicit.
const FIELDS: [string, string][] = [
  ["age", "years"], ["sex", ""], ["gender", ""], ["height", "m"], ["weight", "kg"], ["diagnosis", ""], ["diagnosis_confirmation", ""], ["affected_side", "right|left"],
  ["onset_date", "ISO date"], ["procedure", "none|…"], ["procedure_date", ""], ["procedure_type", "repair|reconstruction"], ["associated_procedures", "[]"],
  ["surgeon_restrictions", ""], ["brace_restrictions", ""], ["weight_bearing_restrictions", ""], ["rom_restrictions", ""], ["pain_rest", "0-10"], ["pain_movement", "0-10"], ["pain_night", "0-10"],
  ["irritability", "high|moderate|low"], ["severity", ""], ["injury_location", ""], ["rom_active", "{…} deg"], ["rom_passive", "{…} deg"], ["functional_limitations", "[]"], ["goals", "[]"],
  ["intended_activity", "[]"], ["comorbidities", "[]"], ["concerning_findings", "[] = screened, none"], ["prior_interventions", "[]"], ["prior_session_response", "better|same|worse"],
  ["exercise_tolerance", ""], ["equipment", "[]"], ["can_assume_starting_position", "true|false"], ["session_time_minutes", "min"], ["language", ""], ["functional_criteria_met", "true|false"],
];

function parse(v: string): unknown {
  const t = v.trim();
  if (t === "") return null;
  try { return JSON.parse(t); } catch { return t; }
}

function Timeline({ rows }: { rows: any[] }) {
  return (
    <table className="timeline"><thead><tr><th>Time window</th><th>Phase and goal</th><th>Exercises</th><th>Schedule</th><th>Restrictions</th><th>Review checkpoint</th><th>Advance / hold / regress</th><th>Basis and gaps</th></tr></thead>
      <tbody>{rows.map((r) => <tr key={r.phase_key}>
        <td>{r.time_window.text ?? `${r.time_window.start ?? "?"}–${r.time_window.end ?? "?"} ${r.time_window.unit}`}<br /><span className="muted">anchor {r.time_window.anchor} · {r.time_window.provisional ? "provisional" : "confirmed"}</span></td>
        <td><b>{r.phase_name}</b><br />{r.goals.join("; ")}</td>
        <td>{r.exercises.map((e: any) => <div key={e.variant_version_id} style={{ marginBottom: 6 }}><b>{e.variant_name}</b> <span className="mono muted">{e.variant_version_id.slice(0, 8)}</span> {e.media_state !== "not_requested" && <Badge kind={stateKind(e.media_state)}>{e.media_state}</Badge>}
          <ol style={{ margin: "2px 0 2px 16px", padding: 0 }}>{e.instructions.map((s: string, i: number) => <li key={i}>{s}</li>)}</ol>
          <div className="muted">source: {e.source_references.map((s: any) => `${s.publisher ?? ""} ${s.title ?? ""} ${s.locator ? JSON.stringify(s.locator) : ""}`).join(" | ")}</div></div>)}</td>
        <td>{r.exercises.map((e: any) => <div key={e.variant_version_id}><Badge kind={e.dose_label === "prescribed" ? "ok" : e.dose_label === "protocol_example" ? "hold" : "warn"}>{e.dose_label.replace("_", " ")}</Badge> {Object.entries(e.prescribed_dose?.fields ?? {}).map(([k, v]: [string, any]) => v.value != null ? `${k} ${v.value}${v.unit ? " " + v.unit : ""}` : `${k}: null (${v.null_reason})`).join("; ")}</div>)}<div className="muted">{r.schedule_note}</div></td>
        <td>{r.unknown_restrictions ? <Badge kind="warn">restrictions unknown</Badge> : null} {r.restrictions.join("; ")}</td>
        <td>{r.review_checkpoint}</td>
        <td><div><b>advance:</b> {r.advance_criteria.join("; ") || "—"}</div><div><b>hold:</b> {r.hold_criteria.join("; ") || "—"}</div><div><b>regress:</b> {r.regress_criteria.join("; ") || "—"}</div></td>
        <td><div>{r.basis.join("; ")}</div><div style={{ color: "var(--warn)" }}>{r.gaps.join("; ")}</div></td>
      </tr>)}</tbody></table>
  );
}

export default function PlanOptions() {
  const { session } = useAuth();
  const [caseRef, setCaseRef] = useState("case-" + Math.random().toString(36).slice(2, 8));
  const [narrative, setNarrative] = useState("55-year-old slightly obese man with frozen shoulder");
  const [fields, setFields] = useState<Record<string, { status: Status; value: string; unit: string }>>({});
  const [resp, setResp] = useState<any>(null);
  const [err, setErr] = useState<unknown>(null);
  const [busy, setBusy] = useState(false);
  const [revision, setRevision] = useState<number>(1);
  const [selected, setSelected] = useState<string | null>(null);
  const [approval, setApproval] = useState<any>(null);
  const [rationale, setRationale] = useState("");
  const setF = (k: string, p: Partial<{ status: Status; value: string; unit: string }>) => {
    const cur = fields[k] ?? { status: "known" as Status, value: "", unit: "" };
    setFields({ ...fields, [k]: { ...cur, ...p } });
  };
  const body = () => ({ case_ref: caseRef, narrative: narrative || null, intake: { fields: Object.fromEntries(Object.entries(fields).filter(([, f]) => f.status !== "known" || f.value !== "").map(([k, f]) => [k, { status: f.status, value: f.status === "known" ? parse(f.value) : null, unit: f.unit || null, provenance: "pt_entered" } as Field])) } });
  const submit = async () => {
    setBusy(true); setErr(null); setApproval(null); setSelected(null);
    try { const r = await post<any>("/plan-options", body()); setResp(r); setRevision(1); } catch (e) { setErr(e); } finally { setBusy(false); }
  };
  const select = async (optionId: string) => {
    setErr(null);
    try { const r = await patch<any>(`/plans/${resp.plan_id}/draft`, { expected_revision: revision, selected_option_id: optionId, rationale: rationale || undefined }); setRevision(r.revision); setSelected(optionId); } catch (e) { setErr(e); }
  };
  const approve = async () => {
    setErr(null);
    try { const r = await post<any>(`/plans/${resp.plan_id}/approve`, { expected_revision: revision, attestation: "Reviewed and approved by PT" }, `${resp.plan_id}-${revision}`); setApproval(r); } catch (e) { setErr(e); }
  };
  return (
    <>
      <h1>Case intake and plan comparison</h1>
      <div className="panel">
        <div className="row">
          <label className="f">Case reference (pseudonymous)<input value={caseRef} onChange={(e) => setCaseRef(e.target.value)} /></label>
          <label className="f" style={{ flex: 1 }}>Narrative (proposed data until confirmed)<input value={narrative} onChange={(e) => setNarrative(e.target.value)} data-testid="narrative" /></label>
          <button className="primary" onClick={submit} disabled={busy || !hasRole(session, "pt", "clinical_lead")} data-testid="submit-case">Get plan options</button>
        </div>
        <details style={{ marginTop: 8 }}><summary className="small">Structured intake ({Object.keys(fields).length} fields set) — every field distinguishes known / unknown / not assessed / not applicable</summary>
          <table><thead><tr><th>Field</th><th>Status</th><th>Value</th><th>Unit</th></tr></thead>
            <tbody>{FIELDS.map(([k, hint]) => <tr key={k}><td>{k}</td>
              <td><select value={fields[k]?.status ?? "unknown"} onChange={(e) => setF(k, { status: e.target.value as Status })}>{["unknown", "known", "not_assessed", "not_applicable"].map((s) => <option key={s}>{s}</option>)}</select></td>
              <td><input value={fields[k]?.value ?? ""} placeholder={hint} onChange={(e) => setF(k, { value: e.target.value })} disabled={(fields[k]?.status ?? "unknown") !== "known"} style={{ width: "100%" }} /></td>
              <td><input value={fields[k]?.unit ?? ""} onChange={(e) => setF(k, { unit: e.target.value })} style={{ width: 70 }} /></td></tr>)}</tbody></table>
        </details>
      </div>
      <Err e={err} />
      {resp && (
        <div data-testid="plan-result">
          <div className="row"><h2 style={{ margin: 0 }}>Result</h2><Badge kind={stateKind(resp.status)}>{resp.status}</Badge>
            <span className="small muted">candidate condition(s): {resp.candidate_conditions.join(", ") || "none recognized"} · release {resp.catalog_release_id?.slice(0, 8)} · assignable {String(resp.assignable)}</span></div>
          {resp.blocked && <div className="error"><b>{resp.blocked.rule_name}</b>: {resp.blocked.message}</div>}
          {resp.missing_fields.length > 0 && <div className="panel"><h3>Missing clinical inputs (prescription = null until supplied)</h3><ul>{resp.missing_fields.map((m: any) => <li key={m.field}><b>{m.field}</b> — {m.reason} {m.required_by && <span className="muted small">({m.required_by})</span>}</li>)}</ul></div>}
          {resp.assumptions.length > 0 && <div className="notice small"><b>Assumptions / notes:</b> {resp.assumptions.join(" ")}</div>}
          {resp.segment_explanations.length > 0 && <div className="panel"><h3>Population segmentation</h3><table><thead><tr><th>Dimension</th><th>Observed</th><th>Classification</th><th>Effect on plan</th><th>Evidence gap</th></tr></thead>
            <tbody>{resp.segment_explanations.map((s: any) => <tr key={s.dimension}><td>{s.dimension}</td><td>{s.observed}</td><td>{s.classification} <span className="muted small">{s.classification_version}</span></td><td>{s.effect}{s.rule_version_id && <Badge kind="ok">rule {s.rule_version_id.slice(0, 8)}</Badge>}</td><td className="small muted">{s.evidence_gap}</td></tr>)}</tbody></table></div>}
          {resp.options.map((o: any) => (
            <div className="panel" key={o.option_id} style={selected === o.option_id ? { borderColor: "var(--accent)" } : undefined}>
              <div className="row"><h3 style={{ margin: 0 }}>{o.label}</h3>{o.validation.passed ? <Badge kind="ok">validation passed</Badge> : <Badge kind="bad">blocked: {o.validation.blockers.length}</Badge>}
                <span className="small muted">est. session {o.estimated_session_minutes ?? "?"} min · equipment {o.equipment.join(", ") || "none"} · uncovered goals {o.uncovered_goals.join(", ") || "none"}</span>
                <span className="spacer" />
                {hasRole(session, "pt", "clinical_lead") && !approval && <button onClick={() => select(o.option_id)} data-testid={`select-${o.option_id}`}>{selected === o.option_id ? "Selected" : "Select as draft"}</button>}</div>
              {o.validation.blockers.map((b: string) => <div key={b} className="error small">{b}</div>)}
              {o.validation.warnings.map((w: string) => <div key={w} className="notice small">{w}</div>)}
              <Timeline rows={o.timeline} />
              <div className="small muted">evidence limitations: {o.evidence_limitations.join("; ")} · progression: {o.progression_requirements.join("; ") || "—"} · reassessment: {o.reassessment?.timing ?? "—"} ({o.reassessment?.provenance})</div>
              <details><summary className="small">rank explanation (35/25/20/20 hypothesis weights)</summary><pre>{JSON.stringify(o.rank_explanation, null, 2)}</pre></details>
            </div>
          ))}
          {selected && !approval && <div className="panel row"><label className="f" style={{ flex: 1 }}>Rationale (must match the structured prescription)<input value={rationale} onChange={(e) => setRationale(e.target.value)} /></label>
            <button className="primary" onClick={approve} data-testid="approve-plan">Approve revision {revision}</button></div>}
          {approval && <div className="panel"><Badge kind="ok">approved</Badge> revision {approval.revision} · signature <span className="mono">{approval.approval_signature.slice(0, 24)}…</span> · hash <span className="mono">{approval.content_hash.slice(0, 16)}…</span><div className="small muted">MoveAI retrieves exactly this revision through <span className="mono">GET /plans/{resp.plan_id}/approved</span>. Any clinical edit invalidates the signature.</div></div>}
          {resp.pathway_previews.length > 0 && <>
            <h2>Conditional pathway previews</h2>
            {resp.pathway_previews.map((p: any) => <div className="panel" key={p.protocol_version_id}>
              <div className="row"><b>{p.protocol_name}</b> <Badge kind={stateKind(p.approval_state)}>{p.approval_state}</Badge> <Badge kind="hold">{p.label}</Badge><span className="small muted">{p.condition} · branch: {p.branch_conditions.join("; ")}</span></div>
              <Timeline rows={p.timeline} />
              <div className="small muted">recovery horizon: {p.recovery_horizon ?? "not stated"} · {p.assumptions.join(" ")}</div>
            </div>)}
          </>}
          {resp.excluded_candidates.length > 0 && <details><summary className="small">excluded candidates ({resp.excluded_candidates.length})</summary><ul className="small">{resp.excluded_candidates.map((e: any, i: number) => <li key={i}>{e.reason} <span className="mono muted">{e.variant_version_id?.slice(0, 8) ?? e.protocol_version_id?.slice(0, 8)}</span></li>)}</ul></details>}
          <details><summary className="small">raw response</summary><pre>{JSON.stringify(resp, null, 2)}</pre></details>
        </div>
      )}
    </>
  );
}
