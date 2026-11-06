// Mock agent runs: an investigation that takes a few seconds, then appears
// everywhere a real one would (report, flag list, Approvals, audit, Agent card).
// The report is built only from the flag's own numbers, so it is grounded for real,
// and its action is only ever 'proposed'; approving it is the normal Approvals step.
import { ApiError, mockVariant } from "../lib/api";
import { factorLabel, supplierLabel } from "../lib/format";
import type {
  AgentSummary,
  Approvals,
  AuditEntry,
  FlagDetail,
  Investigation,
  InvestigationRunState,
  Span,
} from "../lib/types";

export const INVESTIGATING_MS = 2500;
export const RECORDING_MS = 1000;

export interface MockContext {
  /** Fixture route body, or undefined. */
  route: (path: string) => { status: number; body: unknown } | undefined;
  /** The flag behind an event, from the DataCo or the dataset fixtures. */
  flag: (event: string) => FlagDetail | undefined;
  approvals: () => Approvals;
}

interface Run {
  run_id: string;
  event_id: string;
  started: number;
  done: boolean;
}

const runs = new Map<string, Run>();
const completed = new Map<string, Investigation>();
const agentAudit: AuditEntry[] = [];
let nextSeq = 2000;

/** For tests: forget runs and their results. */
export function resetAgentMock() {
  runs.clear();
  completed.clear();
  agentAudit.length = 0;
}

function report(flag: FlagDetail, model: string, actionId: string): Investigation {
  const name = supplierLabel(flag.supplier_key, flag.supplier_name);
  const factor = flag.top_factor;
  const now = new Date().toISOString();
  const number = (value: number | null | undefined): Span => ({
    kind: "number",
    text: (value ?? 0).toFixed(3),
    tool: "get_flag",
  });
  const summary_spans: Span[] = [
    { kind: "text", text: `${name} moved to ${flag.new_tier} in ${flag.quarter} with a score of ` },
    number(flag.score),
    { kind: "text", text: `. ${factorLabel(factor.name)} is the top factor, contributing ` },
    number(factor.weighted_contribution),
    { kind: "text", text: " of it." },
  ];
  return {
    event_id: flag.event_id,
    investigation_id: crypto.randomUUID(),
    status: "proposed",
    grounded: true,
    grounded_ratio: 1,
    ungrounded_numbers: [],
    steps: 2,
    prompt_tokens: 0,
    completion_tokens: 0,
    total_tokens: 0,
    model,
    created_at: now,
    runs: 1,
    human_decision: null,
    decided_by: null,
    decision_reason: null,
    decided_at: null,
    report: {
      summary: summary_spans.map((span) => span.text).join(""),
      summary_spans,
      top_factor: factor.name,
      evidence: [{ tool_name: "get_flag", value: `score=${flag.score.toFixed(3)}`, spans: [{ kind: "text", text: "score=" }, number(flag.score)] }],
      proposed_action: "watchlist",
      confidence: "medium",
    },
    tool_calls: [
      { step: 0, name: "get_flag", ok: true },
      { step: 0, name: "recall_past_investigations", ok: true },
    ],
    recalled: [],
    action: { action_id: actionId, action_type: "watchlist", status: "proposed" },
  };
}

function modelName(context: MockContext): string {
  return (context.route("/api/health")?.body as { model?: string } | undefined)?.model ?? "mock-model";
}

function complete(run: Run, context: MockContext) {
  const flag = context.flag(run.event_id);
  if (!flag) return;
  const actionId = crypto.randomUUID();
  const investigation = report(flag, modelName(context), actionId);
  completed.set(run.event_id, investigation);
  const ts = investigation.created_at;
  const entry = (event: string, detail: Record<string, unknown>): AuditEntry => ({
    seq: nextSeq++,
    ts,
    actor: "agent",
    event,
    detail: { event_id: run.event_id, ...detail },
  });
  const proposed = entry("action_proposed", { action_id: actionId, action_type: "watchlist" });
  agentAudit.unshift(proposed, entry("investigation_proposed", { investigation_id: investigation.investigation_id }));
  // Only a proposal: it joins the Approvals queue and waits for a named human.
  const approvals = context.approvals();
  approvals.items.unshift({
    action_id: actionId,
    event_id: run.event_id,
    supplier_key: flag.supplier_key,
    quarter: flag.quarter,
    action_type: "watchlist",
    status: "proposed",
    proposed_at: ts,
    decided_by: null,
    grounded_ratio: 1,
    audit: [proposed],
  });
  approvals.pending += 1;
}

function runState(run: Run, context: MockContext): InvestigationRunState {
  const elapsed = Date.now() - run.started;
  const finished = elapsed >= INVESTIGATING_MS + RECORDING_MS;
  if (finished && !run.done) {
    run.done = true;
    complete(run, context);
  }
  const recording = elapsed >= INVESTIGATING_MS;
  const investigation = completed.get(run.event_id);
  return {
    run_id: run.run_id,
    kind: "investigation",
    event_id: run.event_id,
    status: finished ? "succeeded" : "running",
    stage: recording ? "recording" : "investigating",
    error: null,
    started_at: new Date(run.started).toISOString(),
    finished_at: finished ? new Date().toISOString() : null,
    stages: [
      { name: "investigating", status: recording ? "done" : "running" },
      { name: "recording", status: finished ? "done" : recording ? "running" : "pending" },
    ],
    investigation_id: finished ? (investigation?.investigation_id ?? null) : null,
    investigation_status: finished ? "proposed" : null,
    grounded: finished ? true : null,
    action_id: finished ? (investigation?.action?.action_id ?? null) : null,
  };
}

function summary(context: MockContext, routeBodies: { status: number; body: unknown }[], audit: AuditEntry[]): AgentSummary {
  const recorded = routeBodies.filter((route) => route.status === 200).map((route) => route.body as Investigation);
  const total = recorded.length + completed.size;
  const grounded = recorded.filter((item) => item.grounded).length + completed.size;
  const last = [...agentAudit, ...audit].find((entry) => entry.actor === "agent");
  return {
    model: modelName(context),
    investigations: total,
    grounded,
    grounded_rate: total ? grounded / total : null,
    pending_approvals: context.approvals().pending,
    last_investigation_at: last?.ts ?? null,
    last_agent_action: last
      ? {
          seq: last.seq,
          ts: last.ts,
          event: last.event,
          event_id: typeof last.detail.event_id === "string" ? last.detail.event_id : null,
        }
      : null,
  };
}

export function agentAuditEntries(): AuditEntry[] {
  return agentAudit;
}

export function completedInvestigation(eventId: string): Investigation | undefined {
  return completed.get(eventId);
}

/** Agent routes; undefined when the path is not one of them. */
export function mockAgentFetch(
  path: string,
  init: RequestInit | undefined,
  context: MockContext,
  investigations: { status: number; body: unknown }[],
): unknown {
  const start = path.match(/^\/api\/investigations\/([^/]+)\/run$/);
  if (start && init?.method === "POST") {
    const eventId = decodeURIComponent(start[1]);
    if (mockVariant() === "nokey") {
      throw new ApiError(400, "The agent is not configured: LLM_API_KEY is not set. Add it to .env and restart the API.");
    }
    if (mockVariant() === "busy") {
      throw new ApiError(
        409,
        "Another writer is running (a pipeline run is writing to the warehouse (provenance_full_pipeline)). Try again when it finishes.",
        "busy",
      );
    }
    if (!context.flag(eventId)) throw new ApiError(404, "No such event.");
    const run: Run = { run_id: crypto.randomUUID(), event_id: eventId, started: Date.now(), done: false };
    runs.set(run.run_id, run);
    return { run_id: run.run_id, event_id: eventId, status: "queued" };
  }
  if (path.startsWith("/api/runs/")) {
    const run = runs.get(decodeURIComponent(path.slice("/api/runs/".length)));
    return run ? runState(run, context) : undefined;
  }
  if (path === "/api/agent/summary") {
    const audit = (context.route("/api/audit?limit=50")?.body as { entries: AuditEntry[] }).entries;
    return summary(context, investigations, audit);
  }
  return undefined;
}
