import { createContext, useContext, useEffect, useMemo, useState } from "react";
import { get, loadSession, saveSession, type Session } from "./api/client";

interface User { id: string; email: string; display_name: string; roles: string[]; tenant_id: string | null }
interface Auth { session: Session | null; users: User[]; setSession: (s: Session | null) => void; refresh: () => Promise<void> }

const Ctx = createContext<Auth>({ session: null, users: [], setSession: () => undefined, refresh: async () => undefined });

// Dev role switcher backed by the header shim. In production the Supabase session provides the same `Session` shape.
export function AuthProvider({ children }: { children: React.ReactNode }) {
  const [session, setSessionState] = useState<Session | null>(loadSession());
  const [users, setUsers] = useState<User[]>([]);
  const setSession = (s: Session | null) => { saveSession(s); setSessionState(s); };
  const refresh = async () => {
    try { const r = await get<{ items: User[] }>("/users"); setUsers(r.items); } catch { setUsers([]); }
  };
  useEffect(() => { if (session) void refresh(); }, [session?.userId]);
  const value = useMemo(() => ({ session, users, setSession, refresh }), [session, users]);
  return <Ctx.Provider value={value}>{children}</Ctx.Provider>;
}

export const useAuth = () => useContext(Ctx);

// STAGING CONVENIENCE, mirrors services/api/moveai_api/auth.py `_shim`: the API widens `source_admin` to every
// role the account actually holds (dev-login gives a new user all of them), so the UI has to agree or a request
// the backend would accept would still hide its own button. This is not itself a security boundary — the API
// enforces the real one — it just keeps the two from disagreeing about what is possible. Remove alongside the
// backend widening once real per-person accounts and roles replace this single-operator period.
export const hasRole = (s: Session | null, ...roles: string[]) => !!s && (s.role === "source_admin" || roles.includes(s.role));
