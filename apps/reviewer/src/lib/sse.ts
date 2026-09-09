// Server-sent events with reconnect and last-event-id; falls back to polling when EventSource cannot carry headers.
// The dev shim needs headers, so we poll `/ingestion-campaigns/{id}/events` here and keep the SSE path for the
// bearer-token deployment (EventSource sends cookies/bearer via the proxy).
import { useEffect, useRef, useState } from "react";
import { API_BASE, authHeaders, get } from "../api/client";

export interface LiveEvent { id: string; campaign_id: string; run_id: string | null; event: string; detail: Record<string, unknown>; at: string }

export function useLiveEvents(onEvent: (e: LiveEvent) => void): { connected: boolean; lastId: number; lastAt: string | null } {
  const [connected, setConnected] = useState(false);
  const [lastAt, setLastAt] = useState<string | null>(null);
  const lastId = useRef(0);
  const cb = useRef(onEvent);
  cb.current = onEvent;
  useEffect(() => {
    let stop = false;
    let es: EventSource | null = null;
    const canSSE = Object.keys(authHeaders()).length === 0;   // bearer/cookie auth: native EventSource works
    if (canSSE) {
      const open = () => {
        es = new EventSource(`${API_BASE}/ingestion-campaigns/stream?after=${lastId.current}`);
        es.onopen = () => setConnected(true);
        es.onerror = () => { setConnected(false); es?.close(); if (!stop) setTimeout(open, 3000); };
        es.addEventListener("campaign", (m) => { const d = JSON.parse((m as MessageEvent).data) as LiveEvent; lastId.current = Number((m as MessageEvent).lastEventId); setLastAt(d.at); cb.current(d); });
        es.addEventListener("heartbeat", () => setLastAt(new Date().toISOString()));
      };
      open();
      return () => { stop = true; es?.close(); };
    }
    const poll = async () => {
      while (!stop) {
        try {
          const r = await get<{ items: { id: number; campaign_id: string; run_id: string | null; event: string; detail: Record<string, unknown>; created_at: string }[] }>(`/ingestion-campaigns/events-all?after=${lastId.current}`);
          setConnected(true);
          for (const e of r.items) { lastId.current = e.id; setLastAt(e.created_at); cb.current({ id: String(e.id), campaign_id: e.campaign_id, run_id: e.run_id, event: e.event, detail: e.detail, at: e.created_at }); }
          if (r.items.length === 0) setLastAt(new Date().toISOString());
        } catch { setConnected(false); }
        await new Promise((res) => setTimeout(res, 3000));
      }
    };
    void poll();
    return () => { stop = true; };
  }, []);
  return { connected, lastId: lastId.current, lastAt };
}
