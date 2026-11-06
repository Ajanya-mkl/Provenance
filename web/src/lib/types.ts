// Shapes returned by src/provenance/api/main.py. Field names match the API exactly.

export type Tier = "low" | "medium" | "high";
export type Confidence = "full" | "low";
export type ActionType = "no_action" | "watchlist" | "request_review";
export type ActionStatus = "proposed" | "approved" | "rejected" | "applied";
export type InvestigationStatus = "proposed" | "rejected_ungrounded" | "no_report";

export interface Health {
  status: "ok" | "degraded";
  model: string;
  approval_required: boolean;
  db_reachable: boolean;
}

export interface FlagSummary {
  event_id: string;
  /** Set for an onboarded dataset's flag; absent for DataCo. */
  dataset_id?: string;
  supplier_key: string;
  supplier_name: string | null;
  quarter: string;
  quarter_start: string;
  old_tier: Tier | null;
  new_tier: Tier;
  score: number;
  confidence: Confidence;
  top_factor: {
    name: string | null;
    weighted_contribution: number | null;
    weighted_contribution_change: number | null;
  };
  queue_status: string | null;
  investigation: { status: InvestigationStatus; grounded: boolean } | null;
  action: { action_type: ActionType; status: ActionStatus } | null;
}

export interface FlagList {
  items: FlagSummary[];
  count: number;
}

export interface FlagDetail {
  event_id: string;
  dataset_id?: string;
  investigation_status: string;
  supplier_key: string;
  supplier_name: string | null;
  quarter: string;
  old_tier: Tier | null;
  new_tier: Tier;
  score: number;
  confidence: Confidence;
  top_factor: {
    name: string;
    raw_value: number | null;
    normalized_value: number | null;
    weight: number | null;
    weighted_contribution: number | null;
    weighted_contribution_change: number | null;
  };
}

export interface SupplierHistory {
  supplier_key: string;
  supplier_name: string;
  versions: {
    version: number;
    delivery_band: string;
    volume_band: string;
    effective_from: string;
    effective_to: string | null;
    is_current: boolean;
  }[];
}

export interface ScoreFactor {
  factor: "late_rate" | "delay_stddev" | "country_risk";
  raw_value: number;
  normalized_value: number;
  weight: number;
  weighted_contribution: number;
  previous_raw_value?: number;
}

export interface ScoreBreakdown {
  supplier_key: string;
  quarter: string;
  score: number;
  tier: Tier;
  confidence: Confidence;
  rules_version: string;
  factors: ScoreFactor[];
  previous_scored_quarter: { quarter: string; score: number; tier: Tier } | null;
  /** A dataset may leave factors out; name -> reason. */
  excluded_factors?: Record<string, string>;
  dataset_id?: string;
  quarter_metrics?: {
    shipment_count: number;
    late_rate: number;
    avg_delay_days: number;
    delay_stddev: number;
    eligible_quarters_to_date: number;
    country_coverage: number;
  };
}

export interface MarketMix {
  supplier_key: string;
  quarter: string;
  previous_quarter: string;
  markets: { market: string; share: number; previous_share?: number; share_change?: number }[];
}

export interface Span {
  kind: "text" | "number";
  text: string;
  tool?: string | null;
}

export interface Investigation {
  event_id: string;
  investigation_id: string;
  status: InvestigationStatus;
  grounded: boolean;
  grounded_ratio: number;
  ungrounded_numbers: string[];
  steps: number;
  prompt_tokens: number;
  completion_tokens: number;
  total_tokens: number;
  model: string;
  created_at: string;
  runs: number;
  human_decision: string | null;
  decided_by: string | null;
  decision_reason: string | null;
  decided_at: string | null;
  report: {
    summary: string;
    summary_spans: Span[];
    top_factor: string;
    evidence: { tool_name: string; value: string; spans: Span[] }[];
    proposed_action: string;
    confidence: string;
  } | null;
  tool_calls: { step: number; name: string; ok: boolean }[];
  recalled: {
    quarter: string;
    old_tier: Tier | null;
    new_tier: Tier;
    score: number | null;
    top_factor: string | null;
    proposed_action: string | null;
    human_decision: string | null;
    decision_reason: string | null;
    event_id: string | null;
  }[];
  action: { action_id: string; action_type: ActionType; status: ActionStatus } | null;
}

export interface AuditEntry {
  seq: number;
  ts: string;
  actor: string;
  event: string;
  detail: Record<string, unknown>;
}

export interface ApprovalItem {
  action_id: string;
  event_id: string;
  supplier_key: string | null;
  quarter: string | null;
  action_type: ActionType;
  status: ActionStatus;
  proposed_at: string;
  decided_by: string | null;
  grounded_ratio: number | null;
  audit: AuditEntry[];
}

export interface Approvals {
  items: ApprovalItem[];
  pending: number;
}

export interface Audit {
  entries: AuditEntry[];
  chain: {
    status: "intact" | "broken";
    broken_seqs: number[];
    rows_verified: number;
    verified_at: string;
  };
}

export interface QualityRow {
  source: string;
  batch_id: string;
  status: string;
  passed: boolean;
  passed_expectations: number;
  failed_expectations: number;
  warning_count: number;
  checked_at: string;
}

export interface Evaluation {
  generated_at: string;
  model: string;
  case_count: number;
  metrics: Record<string, number | null>;
}

// ---------------------------------------------------------------------------
// Onboarded datasets (Phase 10c-2): src/provenance/api/datasets_routes.py.

export type DatasetStatus = "uploaded" | "profiled" | "gated" | "quarantined" | "failed" | "mapped" | "scored";

export interface DatasetItem {
  dataset_id: string;
  name: string;
  status: Exclude<DatasetStatus, "scored">;
  display_status: DatasetStatus;
  row_count: number | null;
  column_count: number | null;
  schema_name: string;
  source_filename: string;
  created_at: string;
}

export interface DatasetColumn {
  column_name: string;
  ordinal: number;
  inferred_type: "string" | "int" | "float" | "bool" | "date";
  type_share: number | null;
  null_pct: number;
  distinct_count: number;
  min_value: string | null;
  max_value: string | null;
  is_candidate_key: boolean;
  looks_like_date: boolean;
  date_format: string | null;
  is_mixed_type: boolean;
  is_mostly_null: boolean;
}

export interface GeneratedRule {
  rule_id: string;
  column: string | null;
  check: string;
  expectation: string;
  severity: "hard" | "tolerance";
  why: string;
}

export interface GateRuleResult {
  rule_id: string;
  column: string | null;
  check: string;
  severity: "hard" | "tolerance";
  why: string;
  success: boolean;
  detail: string;
}

export interface DatasetDetail {
  dataset_id: string;
  name: string;
  source_filename: string;
  status: DatasetItem["status"];
  display_status: DatasetStatus;
  row_count: number | null;
  column_count: number | null;
  schema_name: string;
  encoding: string | null;
  delimiter: string | null;
  created_at: string;
  updated_at: string;
  status_reason: string | null;
  profile_warnings: string[] | null;
  suite: GeneratedRule[] | null;
  gate_report: { passed: boolean; results: GateRuleResult[] } | null;
  columns: DatasetColumn[];
}

export interface UploadReport {
  status: "gated" | "quarantined" | "rejected" | "already_registered" | "failed";
  dataset_id: string | null;
  message: string;
  problems: string[];
  rows_loaded: number;
  profile: {
    row_count: number;
    column_count: number;
    encoding: string;
    delimiter: string;
    warnings: string[];
  } | null;
  gate: {
    passed: boolean;
    hard_failures: number;
    warnings: number;
    rules: GateRuleResult[];
  } | null;
  quarantine_path: string | null;
}

export type OutcomeKind = "late_flag" | "days_pair" | "delay_value";

export interface ContractMapping {
  entity: string | null;
  event_date: { col: string; format: string } | null;
  outcome: {
    kind: OutcomeKind;
    col?: string;
    scheduled_col?: string;
    actual_col?: string;
    unit?: "days" | "hours";
    late_when?: "true" | "false";
  } | null;
  country: string | null;
  cancelled: { col: string; value: string } | null;
  evidence: Record<string, string[]>;
  confidence: number | null;
  notes: string;
}

export type DecisionPath = "heuristic" | "agent" | "heuristic_fallback" | "human";

export interface MappingRecord {
  mapping_id: string;
  dataset_id: string;
  version: number;
  proposal: ContractMapping;
  source: "agent" | "heuristic" | "human";
  status: "proposed" | "approved" | "rejected" | "superseded";
  mappable: boolean;
  scoreable: boolean;
  reasons: { mappable: string; scoreable: string };
  validation: {
    ok: boolean;
    errors: string[];
    decided_by?: DecisionPath;
    escalation_reasons?: string[];
    fallback_reason?: string | null;
    agent_attempt_errors?: string[][];
  };
  created_by: string;
  created_at: string;
  prompt_tokens: number | null;
  completion_tokens: number | null;
  decided_by: string | null;
  reason: string | null;
  decided_at: string | null;
  superseded_by: string | null;
}

export interface MappingState {
  current: MappingRecord | null;
  history: MappingRecord[];
}

export type StageName = "silver" | "metrics" | "scoring" | "flags";
export type StageStatus = "pending" | "running" | "done" | "failed";

export interface RunState {
  run_id: string;
  dataset_id: string;
  status: "queued" | "running" | "succeeded" | "failed";
  stage: StageName | null;
  error: string | null;
  started_at: string | null;
  finished_at?: string | null;
  stages: { name: StageName; status: StageStatus }[];
}

export interface FactorReport {
  name: "late_rate" | "delay_stddev" | "country_risk";
  label: string;
  used: boolean;
  reason: string | null;
  base_weight: number;
  weight: number;
}

export interface DatasetScore {
  entity_key: string;
  entity_name: string | null;
  quarter: string;
  quarter_start: string;
  score: number;
  tier: Tier;
  confidence: Confidence;
}

export interface DatasetResults {
  dataset_id: string;
  rules_version: string;
  bounds_mode: "dataset" | "flagship";
  frozen_at: string | null;
  comparability_note: string;
  last_run: { run_id: string; trigger: string; finished_at: string | null } | null;
  counts: {
    rows_in: number;
    rows_out: number;
    rows_rejected: number;
    entities: number;
    quarters: number;
    scores: number;
    active_flags: number;
    high_in_latest: number;
    latest_quarter: string | null;
  };
  rejects: { reason: string; rows: number }[];
  country: {
    used: boolean | null;
    reason: string | null;
    rows_with_country?: number;
    matched_rows?: number;
    match_rate?: number;
    unmatched_distinct?: number;
    top_unmatched?: { value: string; rows: number }[];
  } | null;
  factors: FactorReport[];
  bounds: Record<string, { min: number; max: number }>;
  scores: DatasetScore[];
  flags: FlagSummary[];
}

export interface DatasetHistory {
  supplier_key: string;
  supplier_name: string | null;
  dataset_id: string;
  scored_quarters: { quarter: string; tier: Tier; score: number }[];
}

// ---------------------------------------------------------------------------
// The agent in the console (Phase 10d).

export interface InvestigationRunState {
  run_id: string;
  kind: "investigation";
  event_id: string;
  status: "queued" | "running" | "succeeded" | "failed";
  stage: "investigating" | "recording" | null;
  error: string | null;
  started_at: string | null;
  finished_at: string | null;
  stages: { name: string; status: StageStatus }[];
  investigation_id: string | null;
  investigation_status: InvestigationStatus | null;
  grounded: boolean | null;
  action_id: string | null;
}

export interface AgentSummary {
  model: string;
  investigations: number;
  grounded: number;
  grounded_rate: number | null;
  pending_approvals: number;
  last_investigation_at: string | null;
  last_agent_action: { seq: number; ts: string; event: string; event_id: string | null } | null;
}
