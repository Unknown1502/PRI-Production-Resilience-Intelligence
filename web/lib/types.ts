/**
 * Types mirroring the Pydantic models in `src/pri/api/schemas.py`.
 *
 * Kept by hand rather than generated, because the generated output for a
 * discriminated union of four move kinds is worse to read than this file and
 * the API surface is small enough to keep honest. If you change a field name
 * in `schemas.py`, change it here.
 */

export type Severity = "HARD" | "SOFT";
export type NodeStatus = "ok" | "impacted" | "downstream";
export type RecoveryMode = "agent" | "deterministic";

export interface Health {
  status: "ok" | "degraded";
  version: string;
  git_sha: string;
  database: "up" | "down";
  agent_enabled: boolean;
  at: string;
}

/** One scene as it appears on the board. Colour comes from these two fields. */
export interface SceneStrip {
  id: string;
  number: string;
  slug: string;
  description: string;
  int_ext: "INT" | "EXT";
  time_of_day: "DAY" | "NIGHT" | "DAWN" | "DUSK";
  estimated_minutes: number;
  cast_count: number;
  vfx_plate: boolean;
}

export interface ScheduleDay {
  date: string;
  call_time: string;
  wrap_time: string;
  location_id: string;
  location_name: string;
  unit: string;
  scene_ids: string[];
  scenes: SceneStrip[];
  scene_count: number;
  scheduled_minutes: number;
  is_reserve: boolean;
  day_kind: string;
}

export interface Schedule {
  production_id: string;
  title: string;
  currency: string;
  version: number;
  shoot_start: string;
  shoot_end: string;
  reserve_days: string[];
  days: ScheduleDay[];
}

export interface StateSnapshot {
  version: number;
  parent_version: number | null;
  event_id: string | null;
  created_at: string;
  digest: string;
  hard_violation_codes: string[];
}

export interface GraphNode {
  id: string;
  type: string;
  label: string;
  status: NodeStatus;
}

export interface GraphEdge {
  id: string;
  source: string;
  target: string;
  kind: string;
}

export interface GraphPayload {
  nodes: GraphNode[];
  edges: GraphEdge[];
}

export interface ImpactReport {
  event_id: string;
  directly_affected_scene_ids: string[];
  downstream_scene_ids: string[];
  affected_cast_ids: string[];
  affected_crew_ids: string[];
  affected_location_ids: string[];
  affected_equipment_ids: string[];
  affected_days: string[];
  downstream_dependency_count: number;
  blast_radius: number;
}

export interface Violation {
  code: string;
  severity: Severity;
  message: string;
  subject_ids: string[];
  observed: string;
  required: string;
}

export interface PlanScore {
  schedule_delay_days: number;
  incremental_cost: string;
  operational_risk: number;
  affected_scene_count: number;
  crew_disruption_hours: number;
  downstream_dependency_impact: number;
}

export interface Move {
  kind: string;
  summary: string;
  detail: Record<string, unknown>;
}

export interface Candidate {
  id: string;
  label: string;
  family: string | null;
  valid: boolean;
  pareto_optimal: boolean;
  moves: Move[];
  violations: Violation[];
  score: PlanScore | null;
  resulting_state_digest: string | null;
}

/**
 * A recovery session as stored, without the derived commentary.
 *
 * `/api/productions/{id}/sessions/latest` returns this so the Recovery screen
 * can rehydrate after a refresh. It carries what the database holds — the
 * candidates and their violations — but not Gemini's explanation or the tool
 * trace, which belong to the run rather than the record.
 */
export interface StoredSession {
  session_id: string;
  production_id: string;
  event_id: string | null;
  base_version: number;
  status: string;
  created_at: string;
  updated_at: string;
  candidates: Candidate[];
}

export interface RoundTrace {
  round_number: number;
  strategy_hints: string[];
  generated_plan_ids: string[];
  invalid_plan_ids: string[];
  failed_rule_codes: string[];
  repaired_plan_ids: string[];
  note: string;
}

export interface ToolCall {
  name: string;
  arguments: Record<string, unknown>;
  summary: string;
  ok: boolean;
}

export interface Recovery {
  session_id: string;
  production_id: string;
  event_id: string;
  base_version: number;
  impact: ImpactReport;
  candidates: Candidate[];
  rounds: RoundTrace[];
  pareto_plan_ids: string[];
  recommended_plan_id: string | null;
  explanation: string;
  tradeoff_summary: string;
  mode: RecoveryMode;
  tool_calls: ToolCall[];
}

export interface CheckResult {
  code: string;
  name: string;
  passed: boolean;
  detail: string;
}

export interface VerificationReport {
  valid: boolean;
  checks: CheckResult[];
  at: string;
}

export interface ExecuteResult {
  session_id: string;
  plan_id: string;
  base_version: number;
  new_version: number;
  digest: string;
  steps_completed: string[];
  artifacts: string[];
  replayed: boolean;
  verification: VerificationReport | null;
}

export interface Artifact {
  id: string;
  version: number;
  kind: string;
  path: string;
  created_at: string;
}

export interface VerificationView {
  production_id: string;
  version: number;
  parent_version: number | null;
  digest: string;
  valid: boolean;
  checks: CheckResult[];
  artifacts: Artifact[];
}

export interface AuditEntry {
  at: string;
  actor: string;
  action: string;
  subject: string;
  detail: Record<string, unknown>;
}

/** The pipeline stages the SSE stream emits, in the order they occur. */
export const STAGES = [
  "EVENT_RECEIVED",
  "IMPACT_COMPUTED",
  "CANDIDATES_GENERATED",
  "CANDIDATE_INVALID",
  "REPLANNING",
  "CANDIDATE_VALID",
  "AWAITING_APPROVAL",
  "APPROVED",
  "EXECUTING",
  "VERIFIED",
  "FAILED",
] as const;

export type Stage = (typeof STAGES)[number];

export interface StreamFrame {
  stage: Stage;
  at: string;
  [key: string]: unknown;
}

/** The seven gates, in the order the transition service enforces them. */
export const TRANSITION_STEPS = [
  "validate_state",
  "validate_constraints",
  "validate_policy",
  "verify_authorization",
  "request_approval",
  "execute",
  "verify_result",
] as const;

export type TransitionStep = (typeof TRANSITION_STEPS)[number];

export interface ApiError {
  error: string;
  detail: string;
  step: string | null;
  rule_code: string | null;
  rule_codes: string[];
}

// ---------------------------------------------------------------------------
// Import pipeline
// ---------------------------------------------------------------------------

/**
 * One thing wrong with an uploaded workbook.
 *
 * Mirrors `pri.importer.errors.ImportIssue` field for field. The review screen
 * renders these records directly, so the shape is a contract: a rename on the
 * Python side is a break here, which is why the API test asserts on the keys.
 */
export interface ImportIssue {
  code: string;
  severity: "ERROR" | "WARNING";
  phase: string;
  message: string;
  fix_hint: string;
  sheet: string | null;
  row: number | null;
  column: string | null;
  offending_value: string | null;
}

/** What was in the file, for the review header. */
export interface ImportSummary {
  scene_count: number;
  person_count: number;
  location_count: number;
  equipment_count: number;
  shooting_day_count: number;
  shoot_span_days: number;
  unit_names: string[];
  first_shoot_date: string | null;
  last_shoot_date: string | null;
}

export type ImportStatus = "PENDING_REVIEW" | "COMMITTED" | "REJECTED" | "FAILED";

/**
 * The staged import, as both the upload and the review route return it.
 *
 * `can_commit` is about blocking errors only. `existing_violations` are
 * constraint breaches already present in the 1st AD's board — they are shown,
 * counted, and never allowed to stop an import.
 */
export interface ImportReport {
  staging_id: string;
  filename: string;
  uploaded_at: string;
  status: ImportStatus;
  summary: ImportSummary;
  errors: ImportIssue[];
  warnings: ImportIssue[];
  existing_violations: Violation[];
  violation_counts_by_code: Record<string, number>;
  can_commit: boolean;
  production_id: string | null;
  committed_version: number | null;
  duplicate_of: string | null;
}

export interface ImportSampleInfo {
  name: string;
  description: string;
  filename: string;
  bytes: number | null;
  available: boolean;
}

export interface ImportSampleList {
  template_version: string;
  samples: ImportSampleInfo[];
}

export interface ImportColumnSpec {
  name: string;
  kind: string;
  required: boolean;
  format: string;
  help: string;
}

export interface ImportSheetSpec {
  name: string;
  required: boolean;
  help: string;
  max_rows: number;
  columns: ImportColumnSpec[];
}

export interface ImportSpec {
  template_version: string;
  /** Authoritative: the browser mirrors it, the server enforces it. */
  max_upload_bytes: number;
  accepted_extensions: string[];
  sheets: ImportSheetSpec[];
}
