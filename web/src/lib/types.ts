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
