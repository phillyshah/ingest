import { NavLink, Navigate, Route, Routes } from "react-router-dom";
import { useEffect, useState } from "react";
import { useAuth } from "./auth";
import Dashboard from "./pages/Dashboard";
import Campaigns from "./pages/Campaigns";
import CampaignDetail from "./pages/CampaignDetail";
import Exercises from "./pages/Exercises";
import Reviews from "./pages/Reviews";
import Sources from "./pages/Sources";
import PlanOptions from "./pages/PlanOptions";
import { API_BASE, get } from "./api/client";
import { WhatsNew } from "./components/WhatsNew";

function SignIn() {
  const { setSession } = useAuth();
  const [username, setUsername] = useState("");
  const [err, setErr] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  // A username, not a UUID. The server resolves it; nobody should have to paste identifiers to look at a board.
  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    setErr(null);
    setBusy(true);
    try {
      const res = await fetch(API_BASE + "/dev-login", {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({ username: username.trim() }),
      });
      const text = await res.text();
      const body = text ? (JSON.parse(text) as { user_id?: string; tenant_id?: string; display_name?: string; role?: string; message?: string }) : null;
      if (!res.ok) throw new Error(body?.message ?? res.statusText);
      setSession({
        userId: body!.user_id!,
        tenantId: body!.tenant_id ?? "",
        role: body!.role ?? "source_admin",
        displayName: body!.display_name,
      });
    } catch (ex) {
      setErr((ex as Error).message);
    } finally {
      setBusy(false);
    }
  };
  return (
    <main>
      <h1>Sign in</h1>
      <div className="panel" style={{ maxWidth: 460 }}>
        <form onSubmit={submit} className="row">
          <label className="f">Username<input value={username} onChange={(e) => setUsername(e.target.value)} placeholder="moveai" autoFocus required /></label>
          <button className="primary" type="submit" disabled={busy || !username.trim()}>{busy ? "Signing in…" : "Sign in"}</button>
        </form>
        {err && <div className="error">{err}</div>}
        <p className="muted small" style={{ marginTop: 10 }}>
          Staging sign-in. There is no password here and no per-person identity — actions are recorded against a
          role, not a person. Production replaces this with Supabase Auth and invitation-only access (spec §22C).
        </p>
      </div>
    </main>
  );
}

export default function App() {
  const { session, setSession, users } = useAuth();
  const [health, setHealth] = useState<"ok" | "down" | "?">("?");
  const [build, setBuild] = useState<{ commit: string; built_at: string; environment: string } | null>(null);
  useEffect(() => { get<{ status: string }>("/healthz").then(() => setHealth("ok")).catch(() => setHealth("down")); }, []);
  // Which commit is serving. After a deploy this is the only honest answer; the repository only says what was pushed.
  useEffect(() => { get<{ commit: string; built_at: string; environment: string }>("/version").then(setBuild).catch(() => setBuild(null)); }, []);
  if (!session) return <SignIn />;
  const me = users.find((u) => u.id === session.userId);
  return (
    <>
      <header className="top">
        <strong>MoveAI Ingest</strong>
        <nav>
          <NavLink to="/" end>Dashboard</NavLink>
          <NavLink to="/campaigns">Campaigns</NavLink>
          <NavLink to="/sources">Sources</NavLink>
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
          <Route path="/sources" element={<Sources />} />
          <Route path="/exercises" element={<Exercises />} />
          <Route path="/exercises/:entityId" element={<Exercises />} />
          <Route path="/reviews" element={<Reviews />} />
          <Route path="/reviews/:table/:id" element={<Reviews />} />
          <Route path="/plan-options" element={<PlanOptions />} />
          <Route path="*" element={<Navigate to="/" />} />
        </Routes>
      </main>
      <WhatsNew build={build} />
    </>
  );
}
