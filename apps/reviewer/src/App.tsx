import { NavLink, Navigate, Route, Routes } from "react-router-dom";
import { useEffect, useState } from "react";
import { useAuth } from "./auth";
import Dashboard from "./pages/Dashboard";
import Campaigns from "./pages/Campaigns";
import CampaignDetail from "./pages/CampaignDetail";
import Exercises from "./pages/Exercises";
import Reviews from "./pages/Reviews";
import PlanOptions from "./pages/PlanOptions";
import { API_BASE, get } from "./api/client";

function SignIn() {
  const { setSession } = useAuth();
  const [userId, setUserId] = useState("");
  const [tenantId, setTenantId] = useState("");
  const [role, setRole] = useState("source_admin");
  const [err, setErr] = useState<string | null>(null);
  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    setErr(null);
    const s = { userId: userId.trim(), tenantId: tenantId.trim(), role };
    try {
      const me = await fetch(API_BASE + "/me", { headers: { "X-User-Id": s.userId, "X-Tenant-Id": s.tenantId, "X-Role": s.role } });
      if (!me.ok) throw new Error(((await me.json()) as { message?: string }).message ?? me.statusText);
      const body = (await me.json()) as { display_name?: string; tenant_id?: string };
      setSession({ ...s, tenantId: body.tenant_id ?? s.tenantId, displayName: body.display_name });
    } catch (ex) { setErr((ex as Error).message); }
  };
  return (
    <main>
      <h1>Sign in</h1>
      <div className="panel" style={{ maxWidth: 560 }}>
        <p className="muted small">Development sign-in (header shim). Production uses Supabase Auth with invitation-only access (spec §22C). Get IDs from <span className="mono">make seed</span>.</p>
        <form onSubmit={submit} className="row">
          <label className="f">User ID<input value={userId} onChange={(e) => setUserId(e.target.value)} placeholder="uuid from make seed" required /></label>
          <label className="f">Tenant ID<input value={tenantId} onChange={(e) => setTenantId(e.target.value)} placeholder="uuid (optional)" /></label>
          <label className="f">Role<select value={role} onChange={(e) => setRole(e.target.value)}>
            {["source_admin", "rights_reviewer", "pt", "clinical_lead", "auditor", "integration"].map((r) => <option key={r}>{r}</option>)}
          </select></label>
          <button className="primary" type="submit">Sign in</button>
        </form>
        {err && <div className="error">{err}</div>}
      </div>
    </main>
  );
}

export default function App() {
  const { session, setSession, users } = useAuth();
  const [health, setHealth] = useState<"ok" | "down" | "?">("?");
  useEffect(() => { get<{ status: string }>("/healthz").then(() => setHealth("ok")).catch(() => setHealth("down")); }, []);
  if (!session) return <SignIn />;
  const me = users.find((u) => u.id === session.userId);
  return (
    <>
      <header className="top">
        <strong>MoveAI Ingest</strong>
        <nav>
          <NavLink to="/" end>Dashboard</NavLink>
          <NavLink to="/campaigns">Campaigns</NavLink>
          <NavLink to="/exercises">Exercises</NavLink>
          <NavLink to="/reviews">Reviews</NavLink>
          <NavLink to="/plan-options">Plan options</NavLink>
        </nav>
        <span className="spacer" />
        <span className={`badge ${health === "ok" ? "ok" : health === "down" ? "bad" : ""}`}>api {health}</span>
        <span className="small">{session.displayName ?? session.userId.slice(0, 8)}</span>
        <select value={session.role} onChange={(e) => setSession({ ...session, role: e.target.value })} title="role (dev switcher; server validates)">
          {(me?.roles ?? [session.role]).map((r) => <option key={r}>{r}</option>)}
        </select>
        <select value={session.userId} onChange={(e) => { const u = users.find((x) => x.id === e.target.value); if (u) setSession({ userId: u.id, tenantId: u.tenant_id ?? session.tenantId, role: u.roles[0], displayName: u.display_name }); }} title="user (dev switcher)">
          {users.map((u) => <option key={u.id} value={u.id}>{u.display_name}</option>)}
          {!me && <option value={session.userId}>{session.userId.slice(0, 8)}</option>}
        </select>
        <button onClick={() => setSession(null)}>Sign out</button>
      </header>
      <main>
        <Routes>
          <Route path="/" element={<Dashboard />} />
          <Route path="/campaigns" element={<Campaigns />} />
          <Route path="/campaigns/:id" element={<CampaignDetail />} />
          <Route path="/exercises" element={<Exercises />} />
          <Route path="/exercises/:entityId" element={<Exercises />} />
          <Route path="/reviews" element={<Reviews />} />
          <Route path="/reviews/:table/:id" element={<Reviews />} />
          <Route path="/plan-options" element={<PlanOptions />} />
          <Route path="*" element={<Navigate to="/" />} />
        </Routes>
      </main>
    </>
  );
}
