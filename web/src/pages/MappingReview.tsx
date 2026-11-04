import { ArrowRight, Bot, Check, Cpu, PenLine, Sparkles, UserRound } from "lucide-react";
import { useId, useMemo, useState } from "react";
import type { ReactNode } from "react";
import { Link, useParams } from "react-router";

import { DatasetFlow, FieldLabel, Verdict } from "../components/datasets";
import { EmptyState, ErrorState, LoadingBlock, Panel, Segmented } from "../components/ui";
import { api, ApiError } from "../lib/api";
import { inputClass, useDatasetDetail } from "../lib/datasetUi";
import { dateTime } from "../lib/format";
import type { ContractMapping, DatasetColumn, DecisionPath, MappingRecord, OutcomeKind } from "../lib/types";
import { useApi } from "../lib/useApi";
import { ColumnTable, DatasetHeader } from "./DatasetProfile";

const REVIEWER_KEY = "provenance.reviewer";

function storedReviewer(): string {
  try {
    return sessionStorage.getItem(REVIEWER_KEY) ?? "";
  } catch {
    return "";
  }
}

const PATH_META: Record<DecisionPath, { label: string; Icon: typeof Bot }> = {
  heuristic: { label: "Heuristic", Icon: Cpu },
  agent: { label: "Agent", Icon: Bot },
  heuristic_fallback: { label: "Heuristic (agent fell back)", Icon: Cpu },
  human: { label: "Human-edited", Icon: UserRound },
};

function PathChip({ record }: { record: MappingRecord }) {
  const path: DecisionPath =
    record.source === "human" ? "human" : (record.validation.decided_by ?? (record.source as DecisionPath));
  const { label, Icon } = PATH_META[path] ?? PATH_META.heuristic;
  return (
    <span className="inline-flex items-center gap-1.5 rounded-full bg-[color-mix(in_srgb,var(--ink)_8%,transparent)] px-2.5 py-0.5 text-[13px] font-semibold text-ink">
      <Icon aria-hidden size={14} /> Decided by: {label}
    </span>
  );
}

/** Editable copy of the mapping's fields, as flat strings. */
interface Draft {
  entity: string;
  dateCol: string;
  dateFormat: string;
  kind: OutcomeKind | "";
  col: string;
  scheduled: string;
  actual: string;
  unit: "days" | "hours";
  lateWhen: "true" | "false";
  country: string;
  cancelledCol: string;
  cancelledValue: string;
}

function toDraft(mapping: ContractMapping): Draft {
  const outcome = mapping.outcome;
  return {
    entity: mapping.entity ?? "",
    dateCol: mapping.event_date?.col ?? "",
    dateFormat: mapping.event_date?.format ?? "",
    kind: outcome?.kind ?? "",
    col: outcome?.col ?? "",
    scheduled: outcome?.scheduled_col ?? "",
    actual: outcome?.actual_col ?? "",
    unit: outcome?.unit ?? "days",
    lateWhen: outcome?.late_when ?? "true",
    country: mapping.country ?? "",
    cancelledCol: mapping.cancelled?.col ?? "",
    cancelledValue: mapping.cancelled?.value ?? "",
  };
}

/** The mapping_store edit paths that differ from the proposal. */
function draftChanges(before: Draft, after: Draft): Record<string, string | null> {
  const paths: [keyof Draft, string][] = [
    ["entity", "entity"],
    ["dateCol", "event_date.col"],
    ["dateFormat", "event_date.format"],
    ["kind", "outcome.kind"],
    ["country", "country"],
  ];
  const outcomePaths: Record<OutcomeKind, [keyof Draft, string][]> = {
    late_flag: [["col", "outcome.col"], ["lateWhen", "outcome.late_when"]],
    days_pair: [["scheduled", "outcome.scheduled_col"], ["actual", "outcome.actual_col"], ["unit", "outcome.unit"]],
    delay_value: [["col", "outcome.col"], ["unit", "outcome.unit"]],
  };
  const sets: Record<string, string | null> = {};
  const kindChanged = before.kind !== after.kind;
  for (const [key, path] of [...paths, ...(after.kind ? outcomePaths[after.kind] : [])]) {
    if (before[key] !== after[key] || (kindChanged && path.startsWith("outcome."))) {
      sets[path] = after[key] === "" ? null : String(after[key]);
    }
  }
  if (before.cancelledCol !== after.cancelledCol || before.cancelledValue !== after.cancelledValue) {
    sets["cancelled.col"] = after.cancelledCol || null;
    sets["cancelled.value"] = after.cancelledCol ? after.cancelledValue || null : null;
  }
  return sets;
}

function ColumnSelect({
  id,
  value,
  onChange,
  columns,
  optional = false,
  disabled,
}: {
  id: string;
  value: string;
  onChange: (value: string) => void;
  columns: DatasetColumn[];
  optional?: boolean;
  disabled?: boolean;
}) {
  return (
    <select id={id} value={value} onChange={(event) => onChange(event.target.value)} className={inputClass} disabled={disabled}>
      <option value="">{optional ? "None" : "Not mapped"}</option>
      {columns.map((column) => (
        <option key={column.column_name} value={column.column_name}>
          {column.column_name} ({column.inferred_type})
        </option>
      ))}
    </select>
  );
}

function FieldRow({
  title,
  hint,
  evidence,
  children,
}: {
  title: string;
  hint: string;
  evidence?: string[];
  children: ReactNode;
}) {
  return (
    <div className="border-b border-line py-4 last:border-0">
      <div className="mb-2 flex items-baseline justify-between gap-3">
        <h3 className="text-[15px] font-semibold">{title}</h3>
        <span className="text-[12px] text-ink-3">{hint}</span>
      </div>
      {children}
      {evidence && evidence.length > 0 && (
        <p className="mt-2 text-[12px] text-ink-3">
          Evidence: <span className="font-mono">{evidence.join(" · ")}</span>
        </p>
      )}
    </div>
  );
}

function ProposalEditor({
  datasetId,
  record,
  columns,
  onChanged,
}: {
  datasetId: string;
  record: MappingRecord;
  columns: DatasetColumn[];
  onChanged: () => void;
}) {
  const base = useMemo(() => toDraft(record.proposal), [record]);
  const [draft, setDraft] = useState<Draft>(base);
  const [reviewer, setReviewer] = useState(storedReviewer);
  const [reason, setReason] = useState("");
  const [busy, setBusy] = useState<string | null>(null);
  const [message, setMessage] = useState<{ kind: "error" | "ok"; text: string } | null>(null);
  const ids = { entity: useId(), date: useId(), format: useId(), kind: useId(), col: useId(), sched: useId(), act: useId(), unit: useId(), country: useId(), ccol: useId(), cval: useId(), by: useId(), reason: useId() };
  const evidence = record.proposal.evidence ?? {};
  const changes = draftChanges(base, draft);
  const dirty = Object.keys(changes).length > 0;
  const locked = record.status !== "proposed";
  const set = <K extends keyof Draft>(key: K, value: Draft[K]) => setDraft((current) => ({ ...current, [key]: value }));
  const byName = reviewer.trim();

  const act = async (label: string, action: () => Promise<unknown>, success: string) => {
    if (!byName) {
      setMessage({ kind: "error", text: "Enter your name as the reviewer first." });
      return;
    }
    try {
      sessionStorage.setItem(REVIEWER_KEY, byName);
    } catch {
      // Not remembered this session; the decision still goes through.
    }
    setBusy(label);
    setMessage(null);
    try {
      await action();
      setMessage({ kind: "ok", text: success });
      onChanged();
    } catch (error) {
      setMessage({ kind: "error", text: error instanceof ApiError ? error.message : String(error) });
    } finally {
      setBusy(null);
    }
  };

  const confidence =
    record.source === "human" ? "Human-edited, no model confidence" : `Confidence ${record.proposal.confidence ?? "–"}`;
  const reasons = record.validation.escalation_reasons ?? [];
  return (
    <Panel
      title={`Proposal v${record.version}`}
      description={`${record.status === "proposed" ? "Waiting for review" : record.status} · ${confidence}`}
      actions={<PathChip record={record} />}
    >
      {reasons.length > 0 && record.source !== "human" && (
        <p className="mb-3 flex gap-2 text-[13px] text-ink-2">
          <Sparkles aria-hidden size={15} className="mt-0.5 shrink-0" />
          <span>The agent was asked because: {reasons.join("; ")}.</span>
        </p>
      )}
      {record.validation.fallback_reason && (
        <p className="mb-3 text-[13px] text-tier-medium">Fell back to the heuristic: {record.validation.fallback_reason}.</p>
      )}

      <FieldRow title="Entity" hint="Supplier, vendor or category (required)" evidence={evidence.entity}>
        <FieldLabel htmlFor={ids.entity}>Column</FieldLabel>
        <ColumnSelect id={ids.entity} value={draft.entity} onChange={(value) => set("entity", value)} columns={columns} disabled={locked} />
      </FieldRow>

      <FieldRow title="Event date" hint="Order or creation date (required)" evidence={evidence.event_date}>
        <div className="grid gap-3 sm:grid-cols-[minmax(0,1.4fr)_minmax(0,1fr)]">
          <div>
            <FieldLabel htmlFor={ids.date}>Column</FieldLabel>
            <ColumnSelect
              id={ids.date}
              value={draft.dateCol}
              columns={columns}
              disabled={locked}
              onChange={(value) => {
                const format = columns.find((column) => column.column_name === value)?.date_format;
                setDraft((current) => ({ ...current, dateCol: value, dateFormat: format ?? current.dateFormat }));
              }}
            />
          </div>
          <div>
            <FieldLabel htmlFor={ids.format}>Format</FieldLabel>
            <input id={ids.format} value={draft.dateFormat} onChange={(event) => set("dateFormat", event.target.value)} className={`${inputClass} font-mono`} disabled={locked} placeholder="%Y-%m-%d" />
          </div>
        </div>
      </FieldRow>

      <FieldRow title="Outcome" hint="How lateness is recorded (required)" evidence={evidence.outcome}>
        <FieldLabel htmlFor={ids.kind}>Kind</FieldLabel>
        <select id={ids.kind} value={draft.kind} onChange={(event) => set("kind", event.target.value as OutcomeKind)} className={inputClass} disabled={locked}>
          <option value="">Not mapped</option>
          <option value="late_flag">Late flag (yes/no column)</option>
          <option value="days_pair">Scheduled and actual durations</option>
          <option value="delay_value">Delay value</option>
        </select>
        {draft.kind === "days_pair" && (
          <div className="mt-3 grid gap-3 sm:grid-cols-2">
            <div>
              <FieldLabel htmlFor={ids.sched}>Scheduled</FieldLabel>
              <ColumnSelect id={ids.sched} value={draft.scheduled} onChange={(value) => set("scheduled", value)} columns={columns} disabled={locked} />
            </div>
            <div>
              <FieldLabel htmlFor={ids.act}>Actual</FieldLabel>
              <ColumnSelect id={ids.act} value={draft.actual} onChange={(value) => set("actual", value)} columns={columns} disabled={locked} />
            </div>
          </div>
        )}
        {(draft.kind === "late_flag" || draft.kind === "delay_value") && (
          <div className="mt-3">
            <FieldLabel htmlFor={ids.col}>{draft.kind === "late_flag" ? "Flag column" : "Delay column"}</FieldLabel>
            <ColumnSelect id={ids.col} value={draft.col} onChange={(value) => set("col", value)} columns={columns} disabled={locked} />
          </div>
        )}
        {draft.kind === "late_flag" && (
          <div className="mt-3">
            <p className="mb-1.5 text-[13px] font-medium text-ink-2">Late when the flag is</p>
            <Segmented<"true" | "false">
              label="Polarity: late when the flag is"
              value={draft.lateWhen}
              onChange={(value) => !locked && set("lateWhen", value)}
              options={[
                { value: "true", label: "True (a late flag)" },
                { value: "false", label: "False (an on-time flag)" },
              ]}
            />
          </div>
        )}
        {(draft.kind === "days_pair" || draft.kind === "delay_value") && (
          <div className="mt-3 max-w-[220px]">
            <FieldLabel htmlFor={ids.unit}>Unit</FieldLabel>
            <select id={ids.unit} value={draft.unit} onChange={(event) => set("unit", event.target.value as "days" | "hours")} className={inputClass} disabled={locked}>
              <option value="days">Days</option>
              <option value="hours">Hours</option>
            </select>
          </div>
        )}
      </FieldRow>

      <FieldRow title="Country" hint="Destination or origin (optional)" evidence={evidence.country}>
        <FieldLabel htmlFor={ids.country}>Column</FieldLabel>
        <ColumnSelect id={ids.country} value={draft.country} onChange={(value) => set("country", value)} columns={columns} optional disabled={locked} />
      </FieldRow>

      <FieldRow title="Cancelled rule" hint="Optional: counted as shipments, not toward lateness">
        <div className="grid gap-3 sm:grid-cols-2">
          <div>
            <FieldLabel htmlFor={ids.ccol}>Column</FieldLabel>
            <ColumnSelect id={ids.ccol} value={draft.cancelledCol} onChange={(value) => set("cancelledCol", value)} columns={columns} optional disabled={locked} />
          </div>
          <div>
            <FieldLabel htmlFor={ids.cval}>Equals</FieldLabel>
            <input id={ids.cval} value={draft.cancelledValue} onChange={(event) => set("cancelledValue", event.target.value)} className={inputClass} disabled={locked || !draft.cancelledCol} placeholder="e.g. Cancelled" />
          </div>
        </div>
      </FieldRow>

      <div className="mt-4 space-y-2 rounded-[14px] bg-[color-mix(in_srgb,var(--ink)_5%,transparent)] p-4">
        <Verdict ok={record.mappable} label="Mappable" reason={record.reasons.mappable} />
        <Verdict ok={record.scoreable} label="Scoreable" reason={record.reasons.scoreable} />
      </div>
      {record.proposal.notes && <p className="mt-3 text-[13px] text-ink-3">Notes: {record.proposal.notes}</p>}

      {locked ? (
        <div className="mt-5 flex flex-wrap items-center justify-between gap-3">
          <p className="text-[14px] text-ink-2">
            {record.status === "approved" ? "Approved" : "Rejected"} by {record.decided_by} · {dateTime(record.decided_at)}
            {record.reason ? ` · “${record.reason}”` : ""}
          </p>
          {record.status === "approved" && (
            <Link to={`/datasets/${datasetId}/run`} className="glass-pill pill-accent px-4 py-2 text-sm">
              Run scoring <ArrowRight aria-hidden size={15} />
            </Link>
          )}
        </div>
      ) : (
        <div className="mt-5 space-y-4">
          <div className="grid gap-3 sm:grid-cols-2">
            <div>
              <FieldLabel htmlFor={ids.by}>Reviewer name (required)</FieldLabel>
              <input id={ids.by} value={reviewer} onChange={(event) => setReviewer(event.target.value)} className={inputClass} maxLength={64} autoComplete="name" />
            </div>
            <div>
              <FieldLabel htmlFor={ids.reason}>Reason (required to reject)</FieldLabel>
              <input id={ids.reason} value={reason} onChange={(event) => setReason(event.target.value)} className={inputClass} maxLength={500} />
            </div>
          </div>
          <div className="flex flex-wrap gap-2">
            <button
              type="button"
              disabled={!dirty || Boolean(busy)}
              onClick={() => act("edit", () => api.editMapping(datasetId, record.mapping_id, byName, changes), "Saved as a new human-edited version.")}
              className="glass-pill px-4 py-2 text-sm"
            >
              <PenLine aria-hidden size={15} /> {busy === "edit" ? "Saving…" : "Save edit"}
            </button>
            <button
              type="button"
              disabled={dirty || !record.mappable || Boolean(busy)}
              title={dirty ? "Save or undo your edit first" : !record.mappable ? "Only a mappable mapping can be approved" : undefined}
              onClick={() => act("approve", () => api.approveMapping(datasetId, record.mapping_id, byName, reason), "Approved. The dataset is mapped.")}
              className="glass-pill pill-accent px-4 py-2 text-sm"
            >
              <Check aria-hidden size={15} /> {busy === "approve" ? "Approving…" : "Approve"}
            </button>
            <button
              type="button"
              disabled={Boolean(busy)}
              onClick={() =>
                reason.trim()
                  ? act("reject", () => api.rejectMapping(datasetId, record.mapping_id, byName, reason), "Rejected.")
                  : setMessage({ kind: "error", text: "A reason is required to reject." })
              }
              className="glass-pill px-4 py-2 text-sm text-danger"
            >
              {busy === "reject" ? "Rejecting…" : "Reject"}
            </button>
            {dirty && (
              <button type="button" onClick={() => setDraft(base)} className="px-2 text-sm text-ink-2 underline">
                Undo changes
              </button>
            )}
          </div>
        </div>
      )}
      {message && (
        <p role={message.kind === "error" ? "alert" : "status"} className={`mt-4 text-[14px] ${message.kind === "error" ? "text-danger" : "text-tier-low"}`}>
          {message.text}
        </p>
      )}
    </Panel>
  );
}

function ProposeButton({ datasetId, onDone }: { datasetId: string; onDone: () => void }) {
  const id = useId();
  const [reviewer, setReviewer] = useState(storedReviewer);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  return (
    <div className="space-y-3">
      <p className="text-[14px] text-ink-2">
        The heuristic maps names and types first; the agent is asked only when the heuristic is unsure. The agent sees
        column names and statistics, never text values.
      </p>
      <div className="max-w-[320px]">
        <FieldLabel htmlFor={id}>Reviewer name</FieldLabel>
        <input id={id} value={reviewer} onChange={(event) => setReviewer(event.target.value)} className={inputClass} />
      </div>
      <button
        type="button"
        disabled={busy}
        onClick={async () => {
          if (!reviewer.trim()) return setError("Enter your name first.");
          setBusy(true);
          setError(null);
          try {
            await api.propose(datasetId, reviewer.trim());
            onDone();
          } catch (caught) {
            setError(caught instanceof ApiError ? caught.message : String(caught));
          } finally {
            setBusy(false);
          }
        }}
        className="glass-pill pill-accent px-4 py-2 text-sm"
      >
        <Sparkles aria-hidden size={15} /> {busy ? "Proposing…" : "Propose a mapping"}
      </button>
      {error && <p role="alert" className="text-[14px] text-danger">{error}</p>}
    </div>
  );
}

export function MappingPage() {
  const { id = "" } = useParams();
  const detail = useDatasetDetail(id);
  const mapping = useApi(`mapping:${id}`, () => api.mapping(id));
  const reload = () => {
    mapping.reload();
    detail.reload();
  };
  const current = mapping.data?.current;
  return (
    <>
      <DatasetHeader detail={detail.data} title="Mapping" />
      <DatasetFlow datasetId={id} status={detail.data?.display_status} />
      <div className="grid gap-6 xl:grid-cols-[minmax(0,0.9fr)_minmax(0,1.1fr)]">
        <Panel title="Profile" description="What the mapper saw.">
          {detail.error ? (
            <ErrorState error={detail.error} onRetry={detail.reload} />
          ) : detail.data ? (
            <ColumnTable columns={detail.data.columns} compact />
          ) : (
            <LoadingBlock rows={6} label="Loading profile" />
          )}
        </Panel>
        <div>
          {mapping.error ? (
            <Panel title="Proposal">
              <ErrorState error={mapping.error} onRetry={mapping.reload} />
            </Panel>
          ) : mapping.loading || !detail.data ? (
            <Panel title="Proposal">
              <LoadingBlock rows={8} label="Loading mapping" />
            </Panel>
          ) : detail.data.status === "quarantined" ? (
            <Panel title="Proposal">
              <EmptyState title="A quarantined dataset cannot be mapped">Fix the file and upload it again.</EmptyState>
            </Panel>
          ) : current ? (
            <ProposalEditor key={current.mapping_id} datasetId={id} record={current} columns={detail.data.columns} onChanged={reload} />
          ) : (
            <Panel title="Proposal">
              <ProposeButton datasetId={id} onDone={reload} />
            </Panel>
          )}
          {mapping.data && mapping.data.history.length > 1 && (
            <p className="mt-3 text-[13px] text-ink-3">
              {mapping.data.history.length} versions; earlier ones are superseded, never overwritten.
            </p>
          )}
        </div>
      </div>
    </>
  );
}
