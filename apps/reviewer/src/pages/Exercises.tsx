import { useState } from "react";
import { Link, useParams, useSearchParams } from "react-router-dom";
import { useConditions, useExercise, useExercises } from "../api/hooks";
import { useAuth, hasRole } from "../auth";
import { Badge, Err, KV, fmt, stateKind } from "../components/ui";

export default function Exercises() {
  const { entityId } = useParams();
  const { session } = useAuth();
  // A link can arrive pre-filtered (a campaign pointing at "the exercises this condition already has"), so the
  // filters start from the query string rather than always from empty.
  const [sp] = useSearchParams();
  const [f, setF] = useState({ q: sp.get("q") ?? "", region: sp.get("region") ?? "", condition: sp.get("condition") ?? "", assistance: "", phase: sp.get("phase") ?? "", equipment: "", setting: "", review_status: "", include_unpublished: hasRole(session, "pt", "clinical_lead", "source_admin", "rights_reviewer", "auditor") });
  const params = "?" + Object.entries(f).filter(([, v]) => v !== "" && v !== false).map(([k, v]) => `${k}=${encodeURIComponent(String(v))}`).join("&");
  const q = useExercises(params);
  const conds = useConditions();
  const one = useExercise(entityId);
  if (entityId) {
    const d = one.data?.latest;
    return (
      <>
        <p><Link to="/exercises">← catalog</Link></p>
        <Err e={one.error} />
        {d && (
          <div className="grid2">
            <div className="panel">
              <h1>{d.record.name}</h1>
              <KV items={[["concept", d.record.concept_id], ["assistance", d.record.assistance], ["region", `${d.record.region} / ${d.record.joint ?? "—"}`], ["setting", (d.record.setting ?? []).join(", ")], ["equipment", (d.record.equipment ?? []).join(", ") || "none"],
                ["state", <Badge kind={stateKind(d.record.approval_state)}>{d.record.approval_state}</Badge>], ["clinical confidence", `${d.clinical_uses?.length ?? 0} use(s); directness ${[...new Set((d.clinical_uses ?? []).map((u: any) => u.directness))].join("/") || "—"}`],
                ["media quality", (d.media ?? []).map((m: any) => m.media_state).join(", ") || "not requested (text-only)"]]} />
              <h3>Instructions</h3><ol>{(d.record.step_sequence ?? []).map((s: any) => <li key={s.step}>{s.text}</li>)}</ol>
              <h3>Source reference</h3><pre>{JSON.stringify(d.record.source_reference, null, 2)}</pre>
              <h3>Version history</h3><table><tbody>{(one.data?.versions ?? []).map((v: any) => <tr key={v.id}><td>v{v.version}</td><td><Badge kind={stateKind(v.approval_state)}>{v.approval_state}</Badge></td><td className="small">{fmt(v.created_at)}</td><td><Link to={`/reviews/exercise_variant_version/${v.id}`}>review</Link></td></tr>)}</tbody></table>
            </div>
            <div className="panel">
              <h3>Clinical uses</h3>
              {(d.clinical_uses ?? []).map((u: any) => <div key={u.id} className="panel"><b>{u.phase}</b> <Badge kind={stateKind(u.approval_state)}>{u.approval_state}</Badge> <span className="muted small">{u.support_category} · {u.directness}</span>
                <div className="small">{u.indication}</div>
                <table className="timeline"><thead><tr><th>Dose field</th><th>Value</th><th>Provenance</th></tr></thead><tbody>{Object.entries(u.dose_envelope ?? {}).map(([k, v]: [string, any]) => <tr key={k}><td>{k}</td><td>{v.value ?? v.range ? JSON.stringify(v.value ?? v.range) : <span className="muted">null — {v.null_reason}</span>}</td><td>{v.provenance}</td></tr>)}</tbody></table>
                {u.exclusion && <div className="notice small">{u.exclusion}</div>}</div>)}
              <h3>Supporting claims</h3>{(d.claims ?? []).map((c: any) => <div key={c.id} className="small"><Badge>{c.claim_type}</Badge> {c.paraphrase} <span className="mono muted">{JSON.stringify(c.locator)}</span></div>)}
              <h3>Diagnostic mappings</h3>{(d.diagnostic_mappings ?? []).map((m: any) => <div key={m.id} className="small"><span className="mono">{m.code}</span> {m.descriptor} <Badge>{m.relationship}</Badge></div>)}
            </div>
          </div>
        )}
      </>
    );
  }
  return (
    <>
      <h1>Exercise catalog</h1>
      <div className="panel row">
        <label className="f">Search<input value={f.q} onChange={(e) => setF({ ...f, q: e.target.value })} placeholder="plain language" /></label>
        <label className="f">Region<input value={f.region} onChange={(e) => setF({ ...f, region: e.target.value })} /></label>
        <label className="f">Condition<select value={f.condition} onChange={(e) => setF({ ...f, condition: e.target.value })}><option value="">any</option>{(conds.data?.items ?? []).map((c) => <option key={c.id} value={c.internal_code}>{c.preferred_name}</option>)}</select></label>
        <label className="f">Assistance<select value={f.assistance} onChange={(e) => setF({ ...f, assistance: e.target.value })}><option value="">any</option>{["passive", "active_assisted", "active", "resisted"].map((a) => <option key={a}>{a}</option>)}</select></label>
        <label className="f">Phase<input value={f.phase} onChange={(e) => setF({ ...f, phase: e.target.value })} /></label>
        <label className="f">Equipment<input value={f.equipment} onChange={(e) => setF({ ...f, equipment: e.target.value })} /></label>
        <label className="f">Setting<select value={f.setting} onChange={(e) => setF({ ...f, setting: e.target.value })}><option value="">any</option><option>home</option><option>supervised</option></select></label>
        <label className="f">Review status<select value={f.review_status} onChange={(e) => setF({ ...f, review_status: e.target.value })}><option value="">default</option>{["pending_review", "approved", "published", "unsigned_placeholder", "rejected", "withdrawn", "invalidated"].map((s) => <option key={s}>{s}</option>)}</select></label>
        <label className="row"><input type="checkbox" checked={f.include_unpublished} onChange={(e) => setF({ ...f, include_unpublished: e.target.checked })} /> include unpublished</label>
      </div>
      <Err e={q.error} />
      <table><thead><tr><th>Variant</th><th>Concept</th><th>Assistance</th><th>Setting</th><th>Clinical confidence</th><th>Media quality</th><th>State</th></tr></thead>
        <tbody>{(q.data?.items ?? []).map((v) => <tr key={v.id}><td><Link to={`/exercises/${v.entity_id}`}>{v.name}</Link> <span className="muted small">v{v.version}</span></td><td>{v.concept_name}</td><td>{v.assistance}</td><td>{(v.setting ?? []).join(", ")}</td>
          <td className="small">{v.clinical_confidence.uses} use(s) · {v.clinical_confidence.directness.join("/") || "—"} · {v.clinical_confidence.support.join("/") || "—"}</td>
          <td className="small">{v.media_quality.states.length ? v.media_quality.states.map((s: string) => <Badge key={s} kind={stateKind(s)}>{s}</Badge>) : <span className="muted">text-only</span>}</td>
          <td><Badge kind={stateKind(v.approval_state)}>{v.approval_state}</Badge></td></tr>)}</tbody></table>
      <p className="small muted">Clinical confidence and media quality are shown separately; popularity never ranks (spec §3C/§7).</p>
    </>
  );
}
