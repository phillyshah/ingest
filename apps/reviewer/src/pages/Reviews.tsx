import { useEffect, useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import { getBlobUrl } from "../api/client";
import { useDecide, useReviewDetail, useReviewQueue } from "../api/hooks";
import { useAuth, hasRole } from "../auth";
import { Badge, Err, KV, fmt, stateKind } from "../components/ui";

/** A media asset that was actually extracted and stored (an embedded PDF photo), not merely referenced by URL.
 * Auth is header-based, so a plain `<img src>` can't fetch it directly — this pulls the bytes through the same
 * client the rest of the app uses and hands the resulting blob to the `<img>` tag. */
function StoredImage({ mediaId }: { mediaId: string }) {
  const [src, setSrc] = useState<string | null>(null);
  const [failed, setFailed] = useState(false);
  useEffect(() => {
    let url: string | null = null;
    getBlobUrl(`/media/${mediaId}/file`)
      .then((u) => { url = u; setSrc(u); })
      .catch(() => setFailed(true));
    return () => { if (url) URL.revokeObjectURL(url); };
  }, [mediaId]);
  if (failed) return <span className="small muted">(could not load the stored image)</span>;
  if (!src) return <span className="small muted">loading photo…</span>;
  return <img src={src} alt="" style={{ maxWidth: 200, maxHeight: 160, display: "block", marginTop: 4, borderRadius: 4 }} />;
}

function Detail({ table, id }: { table: string; id: string }) {
  const { session } = useAuth();
  const nav = useNavigate();
  const d = useReviewDetail(table, id);
  const decide = useDecide();
  const [reason, setReason] = useState("");
  const [edits, setEdits] = useState("");
  const [mergeInto, setMergeInto] = useState("");
  const [start] = useState(Date.now());
  const [result, setResult] = useState<Record<string, unknown> | null>(null);
  useEffect(() => { setResult(null); }, [id]);
  if (d.error) return <Err e={d.error} />;
  if (!d.data) return <p className="muted">loading…</p>;
  const r = d.data.record;
  const act = async (decision: string, extra: Record<string, unknown> = {}) => {
    const out = await decide.mutateAsync({ entity_table: table, version_id: id, decision, reason: reason || undefined, time_spent_seconds: Math.round((Date.now() - start) / 1000), expected_approval_state: r.approval_state, ...extra });
    setResult(out);
    if (out.new_version_id) nav(`/reviews/${table}/${out.new_version_id}`);
  };
  const clinical = hasRole(session, "pt", "clinical_lead");
  const rights = hasRole(session, "rights_reviewer", "clinical_lead");
  const editable = r.approval_state !== "withdrawn" && r.approval_state !== "rejected";
  return (
    <div className="split">
      <div className="panel">
        <h2>Source</h2>
        {d.data.source ? (
          <>
            <KV items={[["publisher", d.data.source.publisher], ["title", d.data.source.title], ["url", <a href={d.data.source.url} target="_blank" rel="noreferrer">{d.data.source.url}</a>], ["document", d.data.source.document_identity], ["retrieved", fmt(d.data.source.retrieved_at)], ["pipeline", <Badge kind={stateKind(d.data.source.pipeline_state)}>{d.data.source.pipeline_state}</Badge>],
              ["excerpt storage", d.data.source.excerpt_allowed ? "allowed" : "not allowed — locator only"]]} />
            <h3>Locator for this record</h3><pre>{JSON.stringify(r.source_reference?.locator ?? r.locator ?? {}, null, 2)}</pre>
            <h3>Claims from this source</h3>
            {d.data.claims.map((c: any) => <div key={c.id} className="small" style={{ marginBottom: 6 }}><Badge>{c.claim_type}</Badge> {c.excerpt ? <q>{c.excerpt}</q> : c.paraphrase} <span className="mono muted">{JSON.stringify(c.locator)}</span>{c.ocr_confidence != null && c.ocr_confidence < 0.8 && <Badge kind="warn">OCR {c.ocr_confidence}</Badge>}{(c.ambiguity_flags ?? []).map((f: string) => <Badge key={f} kind="warn">{f}</Badge>)}</div>)}
            <h3>Rights</h3>{d.data.source.rights ? <pre>{JSON.stringify(Object.fromEntries(Object.entries(d.data.source.rights).filter(([k]) => k.startsWith("can_") || k === "expires_at")), null, 2)}</pre> : <span className="muted">no grant</span>}
          </>
        ) : <p className="muted">Clinician-authored / content-pack record — provenance is the pack itself: <span className="mono">{JSON.stringify(r.source_reference ?? r.provenance)}</span></p>}
      </div>
      <div className="panel">
        <h2>{r.name ?? r.phase ?? table} <Badge kind={stateKind(r.approval_state)}>{r.approval_state}</Badge></h2>
        <Err e={decide.error} />
        {result && <div className="notice small">{JSON.stringify(result)}</div>}
        {d.data.duplicate_proposal && <div className="notice">Proposed duplicate of <Link to={`/reviews/exercise_variant_version/${d.data.duplicate_proposal.id}`}>{d.data.duplicate_proposal.name}</Link> — resolve (merge or reject) before approval. Assistance/load/range differences are never merged.</div>}
        {(d.data.missing_dose_fields ?? []).length > 0 && <div className="notice small"><b>Missing dose fields:</b> {d.data.missing_dose_fields.map((m: any) => `${m.field} (${m.null_reason ?? "no reason"})`).join("; ")}</div>}
        {(d.data.ocr_warnings ?? []).length > 0 && <div className="notice small"><b>OCR warnings:</b> {d.data.ocr_warnings.length} low-confidence value(s)</div>}
        {(d.data.conflicts ?? []).length > 0 && <div className="error small"><b>Conflicting statements:</b> {d.data.conflicts.length}</div>}
        {table === "exercise_variant_version" && <>
          <KV items={[["assistance", r.assistance], ["setting", (r.setting ?? []).join(", ")], ["equipment", (r.equipment ?? []).join(", ") || "none"], ["starting position", r.starting_position], ["version", `v${r.version}`], ["created by", r.created_by?.slice(0, 8)]]} />
          <h3>Instructions</h3><ol>{(r.step_sequence ?? []).map((s: any) => <li key={s.step}>{s.text}</li>)}</ol>
          {(r.extraction_warnings ?? []).length > 0 && <div className="small muted">Flags: {r.extraction_warnings.join("; ")}</div>}
          <h3>Media</h3>
          {(d.data.media ?? []).length ? d.data.media.map((m: any) => (
            <div key={m.id} style={{ marginBottom: 8 }}>
              <Badge kind={stateKind(m.media_state)}>{m.media_state}</Badge>{" "}
              {m.storage_ref ? <StoredImage mediaId={m.id} /> : <span className="small mono">{m.url}</span>}
            </div>
          )) : <span className="muted small">none (text-only is valid)</span>}
          <h3>Diagnostic mappings</h3>{(d.data.diagnostic_mappings ?? []).map((m: any) => <div key={m.id} className="small"><span className="mono">{m.code}</span> {m.descriptor} <Badge>{m.relationship}</Badge> <Badge kind={stateKind(m.approval_state)}>{m.approval_state}</Badge></div>)}
        </>}
        {table !== "exercise_variant_version" && <pre>{JSON.stringify(r, null, 2)}</pre>}
        <h3>Decision</h3>
        <label className="f">Reason<input value={reason} onChange={(e) => setReason(e.target.value)} placeholder="recorded with the decision" /></label>
        {clinical && editable && <div className="row" style={{ marginTop: 8 }}>
          {["draft", "pending_review"].includes(r.approval_state) && <button className="primary" onClick={() => act("approve")} data-testid="approve">Approve</button>}
          <button onClick={() => act("reject")}>Reject</button>
          <button onClick={() => act("request_clarification")}>Request clarification</button>
          {hasRole(session, "clinical_lead") && <button className="danger" onClick={() => act("withdraw")}>Withdraw</button>}
        </div>}
        {clinical && editable && ["exercise_variant_version", "clinical_use_version"].includes(table) && <>
          <label className="f" style={{ marginTop: 8 }}>Edit clinical fields (JSON; creates a new version and invalidates any prior approval)<textarea rows={3} value={edits} onChange={(e) => setEdits(e.target.value)} placeholder='{"cues": ["keep the shoulder relaxed"]}' /></label>
          <div className="row"><button onClick={() => act("edit", { changes: JSON.parse(edits || "{}") })} disabled={!edits}>Save edit</button>
            {table === "exercise_variant_version" && <><input placeholder="merge into entity id" value={mergeInto} onChange={(e) => setMergeInto(e.target.value)} /><button onClick={() => act("merge", { merge_into_entity_id: mergeInto })} disabled={!mergeInto}>Merge</button></>}</div>
        </>}
        {rights && d.data.source?.rights && <div className="row" style={{ marginTop: 8 }}>
          <button onClick={() => decide.mutateAsync({ entity_table: "rights_grant", version_id: d.data.source.rights.id, decision: "rights_allow", reason }).then(setResult)}>Rights: allow all</button>
          <button onClick={() => decide.mutateAsync({ entity_table: "rights_grant", version_id: d.data.source.rights.id, decision: "rights_deny", changes: { can_display_to_patient: true, can_download_media: true }, reason }).then(setResult)}>Rights: deny patient display/media</button>
        </div>}
        <h3>Review events</h3>{(d.data.review_events ?? []).map((e: any) => <div key={e.id} className="small">{fmt(e.created_at)} · {e.reviewer_role} · <b>{e.decision}</b> {e.reason && `— ${e.reason}`} {e.time_spent_seconds != null && <span className="muted">({e.time_spent_seconds}s)</span>}</div>)}
      </div>
    </div>
  );
}

export default function Reviews() {
  const { table, id } = useParams();
  const [t, setT] = useState("exercise_variant_version");
  const q = useReviewQueue(`?entity_table=${t}`);
  return (
    <>
      <h1>PT review queue</h1>
      <div className="row" style={{ marginBottom: 8 }}>
        <select value={t} onChange={(e) => setT(e.target.value)}>{["exercise_variant_version", "clinical_use_version", "protocol_version", "rule_version"].map((x) => <option key={x}>{x}</option>)}</select>
        <span className="muted small">{q.data?.total ?? 0} pending</span>
      </div>
      <Err e={q.error} />
      <div className="split">
        <div className="panel" style={{ maxHeight: "70vh", overflow: "auto" }}>
          <table><thead><tr><th>Item</th><th>Created</th><th>Flags</th></tr></thead>
            <tbody>{(q.data?.items ?? []).map((i) => <tr key={i.version_id} style={i.version_id === id ? { background: "#eef3ff" } : undefined}><td><Link to={`/reviews/${i.entity_table}/${i.version_id}`}>{i.name ?? i.version_id.slice(0, 8)}</Link>{i.duplicate_of_entity_id && <> <Badge kind="warn">dup?</Badge></>}</td><td className="small">{fmt(i.created_at)}</td><td className="small">{(i.flags ?? []).slice(0, 2).join("; ")}</td></tr>)}</tbody></table>
        </div>
        <div>{table && id ? <Detail table={table} id={id} /> : <p className="muted">Select an item. The source excerpt or locator is shown beside the extracted record; editing a clinical field invalidates its earlier approval (spec §3B).</p>}</div>
      </div>
    </>
  );
}
