import { Link } from "react-router-dom";
import { useDashboard } from "../api/hooks";
import { Err, fmt } from "../components/ui";

export default function Dashboard() {
  const q = useDashboard();
  const d = (q.data ?? {}) as Record<string, any>;
  return (
    <>
      <h1>Quality and operations</h1>
      <Err e={q.error} />
      <div className="row">
        {[["active campaigns", d.active_campaigns], ["needs admin action", d.needs_admin_action], ["PT backlog", d.pt_backlog], ["published pathways", d.published_pathways],
          ["spend today (USD)", typeof d.spend_today_usd === "number" ? d.spend_today_usd.toFixed(2) : "—"], ["rights holds", d.rights_holds], ["extraction failures", d.extraction_failures],
          ["stale runs", Array.isArray(d.stale_runs) ? d.stale_runs.length : "—"]].map(([k, v]) => (
          <div className="stat" key={String(k)}><b>{v ?? "—"}</b><span>{k}</span></div>
        ))}
      </div>
      <div className="panel">
        <p className="small muted">Oldest pending review: {fmt(d.oldest_pending_review)}. Counters come from database aggregates, never client state. See <Link to="/campaigns">campaigns</Link> and the <Link to="/reviews">PT queue</Link>.</p>
        <p className="small muted">Internal queue items only: this application never sends unsolicited external messages (spec §3F).</p>
      </div>
    </>
  );
}
