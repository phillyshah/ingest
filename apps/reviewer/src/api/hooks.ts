import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { get, patch, post } from "./client";

export interface Card {
  id: string; title: string; lifecycle: "draft" | "queued" | "running" | "needs_attention" | "pt_review" | "complete" | "closed_incomplete";
  control: "active" | "paused" | "cancelled"; priority: number; owner: string | null; reviewer: string | null; condition_summary: string | null;
  scope_summary: string | null; current_activity: string | null; last_update: string; heartbeat_at: string | null; heartbeat_stale: boolean;
  counts: Record<string, number>; spend_usd: number; cap_usd: number; active_seconds: number; coverage: Record<string, number>;
  blockers: string[]; next_human_action: string | null; allowed_actions: string[]; run_id: string | null; run_revision: number | null;
}
export interface CampaignDetailT extends Card {
  scope: Record<string, unknown>; runs: Record<string, unknown>[]; coverage_checks: Record<string, unknown>[]; limits: Record<string, number>;
  budget: Record<string, unknown>; warnings: string[]; maintenance_enabled: boolean;
  /** What discovery found but could not read, grouped by publisher — the next thing to accept on the Sources page. */
  pending_publishers?: { domain: string; publisher: string | null; review_state: string | null; evidence_state: string | null; count: number; reason: string; pages: string[] }[];
}

export const useCampaigns = (params = "") => useQuery({ queryKey: ["campaigns", params], queryFn: () => get<{ items: Card[]; total: number; server_time: string }>(`/ingestion-campaigns${params}`), refetchInterval: 10_000 });
export const useCampaign = (id?: string) => useQuery({ queryKey: ["campaign", id], queryFn: () => get<CampaignDetailT>(`/ingestion-campaigns/${id}`), enabled: !!id, refetchInterval: 5_000 });
export const useDashboard = () => useQuery({ queryKey: ["dashboard"], queryFn: () => get<Record<string, unknown>>("/ingestion-campaigns/dashboard"), refetchInterval: 15_000 });

export function useCampaignAction(id: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ action, revision, reason }: { action: string; revision?: number | null; reason?: string }) =>
      action === "start" ? post(`/ingestion-campaigns/${id}/runs`) : post(`/ingestion-campaigns/${id}/${action}`, { expected_revision: revision, reason }),
    onSettled: () => { void qc.invalidateQueries({ queryKey: ["campaign", id] }); void qc.invalidateQueries({ queryKey: ["campaigns"] }); },
  });
}

export const useReviewQueue = (params = "") => useQuery({ queryKey: ["queue", params], queryFn: () => get<{ items: QueueItem[]; total: number }>(`/reviews/queue${params}`), refetchInterval: 15_000 });
export interface QueueItem { entity_table: string; version_id: string; name: string | null; created_at: string; flags: string[]; duplicate_of_entity_id: string | null }
export const useReviewDetail = (table?: string, id?: string) => useQuery({ queryKey: ["review", table, id], queryFn: () => get<Record<string, any>>(`/reviews/${table}/${id}`), enabled: !!table && !!id });

export function useDecide() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (body: Record<string, unknown>) => post<Record<string, unknown>>("/reviews", body),
    onSettled: () => { void qc.invalidateQueries({ queryKey: ["queue"] }); void qc.invalidateQueries({ queryKey: ["review"] }); void qc.invalidateQueries({ queryKey: ["exercises"] }); },
  });
}

export const useExercises = (params = "") => useQuery({ queryKey: ["exercises", params], queryFn: () => get<{ items: Record<string, any>[]; total: number }>(`/exercises${params}`) });
export const useExercise = (entityId?: string) => useQuery({ queryKey: ["exercise", entityId], queryFn: () => get<Record<string, any>>(`/exercises/${entityId}`), enabled: !!entityId });
export const useConditions = () => useQuery({ queryKey: ["conditions"], queryFn: () => get<{ items: { id: string; internal_code: string; preferred_name: string }[] }>("/conditions") });

export interface SourcePolicy {
  domain: string; publisher: string; license_id: string; policy_reference: string; scope_note: string | null;
  evidence_state: "uncaptured" | "captured" | "drifted" | "unreachable";
  review_state: "pending" | "signed" | "rejected"; review_note: string | null; reviewed_at: string | null;
  effective: boolean; blocked_by: string | null; terms_excerpt: string | null; terms_fetched_at: string | null;
  permissions: Record<string, "allowed" | "denied" | "unknown">;
  license: { id: string; name: string; url: string | null; summary: string | null; notes: string[] };
}
/** A source as the Files tab lists it: what it is, where it came from, and how far it got. */
export interface SourceRow {
  id: string; canonical_url: string; publisher: string | null; title: string | null; source_type: string;
  allowlist_state: "pending" | "approved" | "denied"; created_at: string; uploaded_by: string | null;
  latest_version_id: string | null; latest_pipeline_state: string | null; latest_version_at: string | null;
  byte_size: number | null; content_type: string | null;
  variants: number; awaiting_review: number; approved: number; photos: number; jobs_active: number; last_problem: string | null;
}
export const useSources = (limit = 200) =>
  useQuery({ queryKey: ["sources", limit], queryFn: () => get<{ items: SourceRow[]; total: number }>(`/sources?limit=${limit}`), refetchInterval: 10_000 });

export const useSourcePolicies = () =>
  useQuery({ queryKey: ["source-policies"], queryFn: () => get<{ items: SourcePolicy[]; readable: number; total: number }>("/source-policies") });

export function useSourcePolicyDecision() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ domain, decision, note }: { domain: string; decision: "sign" | "reject"; note?: string }) =>
      post<SourcePolicy>(`/source-policies/${domain}/decision`, { decision, note }),
    onSettled: () => void qc.invalidateQueries({ queryKey: ["source-policies"] }),
  });
}

export const patchCampaign = (id: string, body: unknown) => patch(`/ingestion-campaigns/${id}`, body);
export const createCampaign = (body: unknown) => post<Card>("/ingestion-campaigns", body);
export const previewScope = (id: string | null, body: unknown) => post<Record<string, any>>(id ? `/ingestion-campaigns/${id}/scope-preview` : "/ingestion-campaigns/preview-unsaved", body);
