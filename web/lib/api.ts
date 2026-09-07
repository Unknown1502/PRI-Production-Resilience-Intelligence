/**
 * Typed client for the PRI API.
 *
 * Every call goes through `request`, so the error shape is handled in exactly
 * one place: the backend returns `{error, detail, step, rule_code}` on failure,
 * and a UI that has to string-match an error message to find the rule code is a
 * UI that will eventually show the wrong one.
 */

import type {
  AuditEntry,
  ImportReport,
  ImportSampleList,
  ImportSpec,
  ExecuteResult,
  GraphPayload,
  Health,
  Recovery,
  Schedule,
  StateSnapshot,
  StoredSession,
  VerificationView,
} from "./types";

/**
 * Every call is same-origin, to the Next.js proxy in app/api/pri/[...path].
 *
 * The backend hostname and the API key are read there, server-side, per
 * request. Nothing in the browser bundle knows either one — which is what lets
 * a judge with no credentials drive the hosted demo, and what stops the image
 * from being pinned to whatever backend URL existed at `docker build` time.
 */
export const API_URL = "/api/pri";

/**
 * The default production. `?production=` overrides it per URL, which is how a
 * freshly imported production becomes viewable without a redeploy.
 */
export const PRODUCTION_ID = "film-001";

/** Mirrors the backend's error envelope, so callers can branch on `ruleCode`. */
export class PriApiError extends Error {
  readonly status: number;
  readonly code: string;
  readonly step: string | null;
  readonly ruleCode: string | null;
  readonly ruleCodes: string[];

  constructor(
    status: number,
    code: string,
    detail: string,
    step: string | null = null,
    ruleCode: string | null = null,
    ruleCodes: string[] = [],
  ) {
    super(detail);
    this.name = "PriApiError";
    this.status = status;
    this.code = code;
    this.step = step;
    this.ruleCode = ruleCode;
    this.ruleCodes = ruleCodes;
  }
}

interface RequestOptions {
  method?: "GET" | "POST";
  body?: unknown;
  /** Read routes are cached briefly; anything that mutates never is. */
  revalidate?: number;
  signal?: AbortSignal;
}

async function request<T>(path: string, options: RequestOptions = {}): Promise<T> {
  const { method = "GET", body, revalidate, signal } = options;

  const headers: Record<string, string> = { Accept: "application/json" };
  if (body !== undefined) {
    headers["Content-Type"] = "application/json";
  }
  // No API key here, deliberately. The proxy adds it server-side; a key in
  // the browser bundle is a key the whole internet has.

  const response = await fetch(`${API_URL}${path}`, {
    method,
    headers,
    body: body === undefined ? undefined : JSON.stringify(body),
    signal,
    ...(revalidate === undefined ? { cache: "no-store" as const } : { next: { revalidate } }),
  });

  if (!response.ok) {
    let code = "http_error";
    let detail = `${response.status} ${response.statusText}`;
    let step: string | null = null;
    let ruleCode: string | null = null;
    let ruleCodes: string[] = [];
    try {
      const parsed = (await response.json()) as Record<string, unknown>;
      code = typeof parsed.error === "string" ? parsed.error : code;
      detail = typeof parsed.detail === "string" ? parsed.detail : detail;
      step = typeof parsed.step === "string" ? parsed.step : null;
      ruleCode = typeof parsed.rule_code === "string" ? parsed.rule_code : null;
      ruleCodes = Array.isArray(parsed.rule_codes) ? (parsed.rule_codes as string[]) : [];
    } catch {
      // A non-JSON body means something upstream of the app failed — a proxy,
      // a cold start. The status line is all we have and all we need.
    }
    throw new PriApiError(response.status, code, detail, step, ruleCode, ruleCodes);
  }

  return (await response.json()) as T;
}

export const api = {
  health: () => request<Health>("/health"),

  schedule: (productionId = PRODUCTION_ID, version?: number) =>
    request<Schedule>(
      `/api/productions/${productionId}/schedule${version ? `?version=${version}` : ""}`,
    ),

  state: (productionId = PRODUCTION_ID, version?: number) =>
    request<StateSnapshot>(
      `/api/productions/${productionId}/state${version ? `?version=${version}` : ""}`,
    ),

  graph: (productionId = PRODUCTION_ID, eventId?: string) =>
    request<GraphPayload>(
      `/api/productions/${productionId}/graph${eventId ? `?event_id=${eventId}` : ""}`,
    ),

  ingestEvent: (productionId: string, event: Record<string, unknown>) =>
    request<{ event_id: string; duplicate: boolean }>(
      `/api/productions/${productionId}/events`,
      { method: "POST", body: event },
    ),

  recover: (productionId: string, eventId: string, strategyHints?: string[]) =>
    request<Recovery>(`/api/productions/${productionId}/recover`, {
      method: "POST",
      body: { event_id: eventId, strategy_hints: strategyHints ?? null },
    }),

  /** The most recent recovery session, or `{session: null}`. */
  latestSession: (productionId = PRODUCTION_ID) =>
    request<{ session: StoredSession | null }>(
      `/api/productions/${encodeURIComponent(productionId)}/sessions/latest`,
    ),

  session: (sessionId: string) =>
    request<Record<string, unknown>>(`/api/sessions/${sessionId}`),

  approve: (sessionId: string, planId: string, approver: string, note?: string) =>
    request<{ approval_id: string; decision: string }>(`/api/sessions/${sessionId}/approve`, {
      method: "POST",
      body: { plan_id: planId, approver, note: note ?? null, decision: "APPROVED" },
    }),

  reject: (sessionId: string, planId: string, approver: string, note?: string) =>
    request<{ approval_id: string; decision: string }>(`/api/sessions/${sessionId}/approve`, {
      method: "POST",
      body: { plan_id: planId, approver, note: note ?? null, decision: "REJECTED" },
    }),

  execute: (sessionId: string, planId: string, approver: string) =>
    request<ExecuteResult>(`/api/sessions/${sessionId}/execute`, {
      method: "POST",
      body: { plan_id: planId, approver },
    }),

  verification: (productionId: string, version: number) =>
    request<VerificationView>(`/api/productions/${productionId}/verification/${version}`),

  audit: (productionId = PRODUCTION_ID, limit = 200) =>
    request<AuditEntry[]>(`/api/productions/${productionId}/audit?limit=${limit}`),

  resetDemo: () =>
    request<{ production_id: string; version: number }>("/api/demo/reset", { method: "POST" }),

  artifactUrl: (artifactId: string) => `${API_URL}/api/artifacts/${artifactId}`,

  // --- import pipeline ---------------------------------------------------
  // The upload itself is not here: it needs XHR for a real progress bar, so
  // `DropZone` owns that one request and uses `uploadUrl`. No key — the proxy
  // adds it.

  uploadUrl: () => `${API_URL}/api/import`,

  importTemplateUrl: () => `${API_URL}/api/import/template`,

  importSamples: () => request<ImportSampleList>("/api/import/samples"),

  importSampleUrl: (name: string) =>
    `${API_URL}/api/import/samples/${encodeURIComponent(name)}`,

  importSpec: () => request<ImportSpec>("/api/import/spec/sheets"),

  importReport: (stagingId: string) =>
    request<ImportReport>(`/api/import/${encodeURIComponent(stagingId)}`),

  commitImport: (stagingId: string, confirmedBy: string) =>
    request<{ production_id: string; version: number }>(
      `/api/import/${encodeURIComponent(stagingId)}/commit`,
      { method: "POST", body: { confirmed_by: confirmedBy } },
    ),

  rejectImport: (stagingId: string, reason: string, rejectedBy: string) =>
    request<{ staging_id: string; status: string; reason: string }>(
      `/api/import/${encodeURIComponent(stagingId)}/reject`,
      { method: "POST", body: { reason, rejected_by: rejectedBy } },
    ),

  exportUrl: (productionId = PRODUCTION_ID) =>
    `${API_URL}/api/productions/${encodeURIComponent(productionId)}/export`,

  streamUrl: (productionId = PRODUCTION_ID) => `${API_URL}/api/stream/${productionId}`,
};

/** Format a Decimal-as-string from the API without losing its precision. */
export function money(value: string | number, currency = "USD"): string {
  const numeric = typeof value === "string" ? Number.parseFloat(value) : value;
  if (Number.isNaN(numeric)) return `${currency} ${value}`;
  return `${currency} ${numeric.toLocaleString("en-US", { maximumFractionDigits: 0 })}`;
}

/** `2026-09-11T08:30:00+05:30` → `08:30`. */
export function clock(iso: string): string {
  return iso.slice(11, 16);
}

/** `2026-09-11` → `Fri 11 Sep`. */
export function shortDate(iso: string): string {
  const date = new Date(`${iso.slice(0, 10)}T00:00:00Z`);
  return date.toLocaleDateString("en-GB", {
    weekday: "short",
    day: "numeric",
    month: "short",
    timeZone: "UTC",
  });
}
