// Thin fetch wrapper. Auth is a dev shim (X-User-Id / X-Tenant-Id / X-Role) behind the same interface Supabase Auth
// will replace: swap `authHeaders()` for a bearer token and nothing else changes.
import type { paths } from "./schema";

export type Paths = paths;
export const API_BASE = (import.meta.env.VITE_API_BASE as string | undefined) ?? "/api/v1";

export interface Session {
  userId: string;
  tenantId: string;
  role: string;
  displayName?: string;
}

const KEY = "moveai.session";

export function loadSession(): Session | null {
  try {
    const raw = localStorage.getItem(KEY);
    return raw ? (JSON.parse(raw) as Session) : null;
  } catch {
    return null;
  }
}

export function saveSession(s: Session | null): void {
  try {
    if (s) localStorage.setItem(KEY, JSON.stringify(s));
    else localStorage.removeItem(KEY);
  } catch {
    /* storage unavailable */
  }
}

export function authHeaders(): Record<string, string> {
  const s = loadSession();
  if (!s) return {};
  return { "X-User-Id": s.userId, "X-Tenant-Id": s.tenantId, "X-Role": s.role };
}

export class ApiError extends Error {
  constructor(public status: number, public code: string, message: string, public details?: unknown) {
    super(message);
  }
}

export async function api<T = unknown>(path: string, init: RequestInit & { json?: unknown; idempotencyKey?: string } = {}): Promise<T> {
  const headers: Record<string, string> = { ...authHeaders(), ...(init.headers as Record<string, string> | undefined) };
  let body = init.body;
  if (init.json !== undefined) {
    headers["content-type"] = "application/json";
    body = JSON.stringify(init.json);
  }
  if (init.idempotencyKey) headers["idempotency-key"] = init.idempotencyKey;
  const res = await fetch(API_BASE + path, { ...init, headers, body });
  const text = await res.text();
  const data = text ? (JSON.parse(text) as unknown) : null;
  if (!res.ok) {
    const e = (data ?? {}) as { code?: string; message?: string; details?: unknown };
    throw new ApiError(res.status, e.code ?? "http_error", e.message ?? res.statusText, e.details);
  }
  return data as T;
}

export const get = <T,>(path: string) => api<T>(path);
export const post = <T,>(path: string, json?: unknown, idempotencyKey?: string) => api<T>(path, { method: "POST", json, idempotencyKey });
export const patch = <T,>(path: string, json?: unknown) => api<T>(path, { method: "PATCH", json });
