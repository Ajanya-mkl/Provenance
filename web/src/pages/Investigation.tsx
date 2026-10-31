import { BadgeCheck, ChevronRight, FileSearch, History, OctagonX, Terminal } from "lucide-react";
import { Link, useParams } from "react-router";

import { ReceiptScope, ReceiptText, SourcesRow } from "../components/Receipts";
import {
  ConfidenceBadge,
  EmptyState,
  ErrorState,
  LoadingBlock,
  PageHeader,
  Panel,
  TierChange,
} from "../components/ui";
import { api } from "../lib/api";
import { ACTION_LABELS, TOOL_LABELS, dateTime, factorLabel, score, supplierLabel } from "../lib/format";
import type { Investigation } from "../lib/types";
import { useApi } from "../lib/useApi";

function GroundedBadge({ investigation }: { investigation: Investigation }) {
  const numbers = investigation.report?.summary_spans.filter((span) => span.kind === "number").length ?? 0;
  return (
    <div className="inline-flex items-center gap-2 rounded-full bg-tier-low/15 px-3.5 py-1.5 text-[14px] font-semibold text-tier-low">
      <BadgeCheck aria-hidden size={17} />
      GROUNDED
      <span className="font-normal text-ink-2">
        · every number traces to a tool result{numbers ? ` (${numbers} in the summary)` : ""}
      </span>
    </div>
  );
}

function NotGroundedBanner({ investigation }: { investigation: Investigation }) {
  return (
    <div
      role="alert"
      className="flex gap-4 rounded-[20px] bg-danger/15 p-5 shadow-[inset_0_0_0_1px_color-mix(in_srgb,var(--danger)_45%,transparent)]"
    >
      <OctagonX aria-hidden size={28} className="shrink-0 text-danger" />
      <div>
        <p className="text-[17px] font-bold tracking-[-0.01em] text-danger">NOT GROUNDED</p>
        <p className="mt-1 max-w-[65ch] text-[15px] text-ink">
          This report contains numbers no tool returned, so it was rejected. Its proposed action is
          hidden and no action was created.
        </p>
        {investigation.ungrounded_numbers.length > 0 && (
          <p className="mt-2 text-[14px] text-ink-2">
            Unsourced:{" "}
            {investigation.ungrounded_numbers.map((number) => (
              <span key={number} className="tabular mr-2 font-semibold text-danger line-through decoration-2">
                {number}
              </span>
            ))}
          </p>
        )}
      </div>
    </div>
  );
}

function ProposedAction({ investigation }: { investigation: Investigation }) {
  const report = investigation.report!;
  const action = investigation.action;
  const status = action?.status;
  return (
    <div className="glass-lite p-5">
      <h2 className="text-[20px] font-semibold tracking-[-0.02em]">
        Proposed: {ACTION_LABELS[action?.action_type ?? report.proposed_action] ?? report.proposed_action}
      </h2>
      <p className="mt-1 text-[14px] text-ink-2">
        {status === "proposed" && "Waiting for a reviewer. Nothing has been applied."}
        {status === "applied" && `Approved by ${investigation.decided_by ?? "a reviewer"} and applied.`}
        {status === "rejected" && `Rejected by ${investigation.decided_by ?? "a reviewer"}.`}
        {status === "approved" && "Approved; waiting to apply."}
        {!action && "Recorded before approvals existed; no action row."}
      </p>
      {investigation.decision_reason && (
        <p className="mt-2 text-[14px] text-ink-3">“{investigation.decision_reason}”</p>
      )}
      {status === "proposed" && (
        <Link to="/approvals" className="glass-pill pill-accent mt-4 px-4 py-2 text-sm">
          Review in approvals
        </Link>
      )}
    </div>
  );
}

export function InvestigationPage() {
  const { eventId = "" } = useParams();
  const flag = useApi(`flag:${eventId}`, () => api.flag(eventId));
  const investigation = useApi(`investigation:${eventId}`, () => api.investigation(eventId));
  const name = flag.data ? supplierLabel(flag.data.supplier_key, flag.data.supplier_name) : "Investigation";

  const header = (
    <PageHeader
      title={name}
      meta={
        flag.data ? (
          <span className="inline-flex flex-wrap items-center gap-x-4 gap-y-1">
            <span>Investigation · {flag.data.quarter}</span>
            <TierChange from={flag.data.old_tier} to={flag.data.new_tier} />
            <span className="tabular">score {score(flag.data.score)}</span>
          </span>
        ) : (
          eventId
        )
      }
      actions={
        <Link to={`/flags?open=${encodeURIComponent(eventId)}`} className="glass-pill px-4 py-2 text-sm">
          Flag detail
        </Link>
      }
    />
  );

  if (investigation.error?.status === 404) {
    return (
      <>
        {header}
        <Panel>
          <EmptyState icon={<FileSearch size={22} />} title="Not investigated yet">
            <p>The agent has not investigated this flag. Run it from a terminal:</p>
            <code className="mt-3 inline-flex items-center gap-2 rounded-[10px] bg-[color-mix(in_srgb,var(--ink)_9%,transparent)] px-3 py-2 text-left font-mono text-[13px] text-ink">
              <Terminal aria-hidden size={14} />
              python -m provenance.agent.run --event-id {eventId}
            </code>
          </EmptyState>
        </Panel>
      </>
    );
  }
  if (investigation.error) {
    return (
      <>
        {header}
        <Panel>
          <ErrorState error={investigation.error} onRetry={investigation.reload} />
        </Panel>
      </>
    );
  }
  if (!investigation.data) {
    return (
      <>
        {header}
        <Panel>
          <LoadingBlock rows={6} label="Loading investigation" />
        </Panel>
      </>
    );
  }

  const data = investigation.data;
  const report = data.report;
  const grounded = data.status === "proposed" && data.grounded;
  return (
    <>
      {header}
      <ReceiptScope>
        <div className="grid gap-6 xl:grid-cols-[minmax(0,1fr)_340px]">
          <div className="space-y-6">
            {grounded ? <GroundedBadge investigation={data} /> : <NotGroundedBanner investigation={data} />}

            <Panel title="Agent report">
              {report ? (
                <div className="space-y-5">
                  <p className="max-w-[68ch] text-[17px] leading-[1.75] text-ink">
                    <ReceiptText spans={report.summary_spans} />
                  </p>
                  <div className="flex flex-wrap gap-x-6 gap-y-2 text-[14px] text-ink-2">
                    <span>
                      Top factor <span className="font-semibold text-ink">{factorLabel(report.top_factor)}</span>
                    </span>
                    <span>
                      Agent confidence <span className="font-semibold text-ink">{report.confidence}</span>
                    </span>
                  </div>
                  <div>
                    <h3 className="mb-2 text-[13px] font-medium text-ink-3">Sources</h3>
                    <SourcesRow tools={data.tool_calls} />
                  </div>
                </div>
              ) : (
                <EmptyState title="No report">The agent stopped before writing a report.</EmptyState>
              )}
            </Panel>

            {report && report.evidence.length > 0 && (
              <Panel title="Evidence" description="Each value with the tool that returned it.">
                <ul className="flex flex-col">
                  {report.evidence.map((item, index) => (
                    <li key={index} className="flex flex-wrap items-baseline gap-x-4 gap-y-1 border-b border-line py-3 last:border-0">
                      <span className="w-[150px] shrink-0 text-[13px] font-medium text-ink-3">
                        {TOOL_LABELS[item.tool_name] ?? item.tool_name}
                      </span>
                      <span className="min-w-0 text-[15px] text-ink">
                        <ReceiptText spans={item.spans} />
                      </span>
                    </li>
                  ))}
                </ul>
              </Panel>
            )}
          </div>

          <aside className="space-y-6">
            {grounded && report && <ProposedAction investigation={data} />}

            <div className="glass-lite p-5">
              <h2 className="mb-3 flex items-center gap-2 text-[15px] font-semibold">
                <History aria-hidden size={16} /> Recalled cases
              </h2>
              {data.recalled.length ? (
                <ul className="flex flex-wrap gap-2">
                  {data.recalled.map((past) => {
                    const label = (
                      <>
                        <span className="tabular font-semibold whitespace-nowrap">{past.quarter}</span>
                        <span className="text-ink-3">
                          {past.old_tier ?? "new"} → {past.new_tier} · {factorLabel(past.top_factor)}
                        </span>
                        {past.human_decision && <span className="text-ink-2">· {past.human_decision}</span>}
                      </>
                    );
                    return (
                      <li key={`${past.quarter}-${past.event_id}`}>
                        {past.event_id ? (
                          <Link
                            to={`/investigations/${encodeURIComponent(past.event_id)}`}
                            className="glass-pill px-3 py-1.5 text-[13px]"
                          >
                            {label}
                            <ChevronRight aria-hidden size={14} />
                          </Link>
                        ) : (
                          <span className="glass-pill px-3 py-1.5 text-[13px]">{label}</span>
                        )}
                      </li>
                    );
                  })}
                </ul>
              ) : (
                <p className="text-[14px] text-ink-2">No earlier grounded investigations of this supplier.</p>
              )}
            </div>

            <dl className="grid grid-cols-2 gap-x-4 gap-y-2 px-1 text-[13px]">
              <dt className="text-ink-3">Steps</dt>
              <dd className="tabular text-ink-2">{data.steps} of 6</dd>
              <dt className="text-ink-3">Tokens</dt>
              <dd className="tabular text-ink-2">
                {data.total_tokens.toLocaleString("en-GB")}{" "}
                <span className="text-ink-3">
                  ({data.prompt_tokens.toLocaleString("en-GB")} in / {data.completion_tokens.toLocaleString("en-GB")} out)
                </span>
              </dd>
              <dt className="text-ink-3">Model</dt>
              <dd className="truncate text-ink-2" title={data.model}>
                {data.model}
              </dd>
              <dt className="text-ink-3">Recorded</dt>
              <dd className="text-ink-2">{dateTime(data.created_at)}</dd>
              {data.runs > 1 && (
                <>
                  <dt className="text-ink-3">Runs</dt>
                  <dd className="tabular text-ink-2">{data.runs} (latest shown)</dd>
                </>
              )}
              <dt className="text-ink-3">Flag</dt>
              <dd className="text-ink-2">{flag.data ? <ConfidenceBadge confidence={flag.data.confidence} /> : "–"}</dd>
            </dl>
            <p className="px-1 text-[12px] leading-relaxed text-ink-3">
              The agent explains the rule-based score; it never overrides it. Hover or focus a number to
              see which tool returned it.
            </p>
          </aside>
        </div>
      </ReceiptScope>
    </>
  );
}

export function InvestigationsIndex() {
  const flags = useApi("flags", () => api.flags());
  const investigated = (flags.data?.items ?? []).filter((flag) => flag.investigation);
  return (
    <>
      <PageHeader
        title="Investigations"
        meta="The agent's latest report for each flag it has investigated."
      />
      <section aria-label="Investigated flags" className="glass p-2">
        {flags.error ? (
          <ErrorState error={flags.error} onRetry={flags.reload} />
        ) : flags.loading ? (
          <div className="p-4">
            <LoadingBlock rows={6} label="Loading investigations" />
          </div>
        ) : investigated.length === 0 ? (
          <EmptyState icon={<FileSearch size={22} />} title="No investigations yet">
            Queue a flag for the agent with python -m provenance.agent.run --next.
          </EmptyState>
        ) : (
          <ul>
            {investigated.map((flag) => (
              <li key={flag.event_id} className="border-b border-line last:border-0">
                <Link
                  to={`/investigations/${encodeURIComponent(flag.event_id)}`}
                  className="grid grid-cols-[minmax(0,1.5fr)_90px_minmax(0,1fr)_20px] items-center gap-4 rounded-[14px] px-4 py-3.5 transition-colors hover:bg-[color-mix(in_srgb,var(--ink)_6%,transparent)]"
                >
                  <span className="truncate text-[15px] font-semibold">
                    {supplierLabel(flag.supplier_key, flag.supplier_name)}
                  </span>
                  <span className="tabular text-[14px] text-ink-2">{flag.quarter}</span>
                  <span
                    className={`inline-flex items-center gap-1.5 text-[13px] font-medium ${
                      flag.investigation?.grounded ? "text-tier-low" : "text-danger"
                    }`}
                  >
                    {flag.investigation?.grounded ? <BadgeCheck aria-hidden size={14} /> : <OctagonX aria-hidden size={14} />}
                    {flag.investigation?.grounded ? "Grounded" : "Not grounded"}
                    {flag.action && (
                      <span className="font-normal text-ink-3">· {ACTION_LABELS[flag.action.action_type]} ({flag.action.status})</span>
                    )}
                  </span>
                  <ChevronRight aria-hidden size={18} className="text-ink-3" />
                </Link>
              </li>
            ))}
          </ul>
        )}
      </section>
    </>
  );
}
