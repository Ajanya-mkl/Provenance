import { ArrowRight, Bot, Upload } from "lucide-react";
import { Link } from "react-router";

import { ActivityFeed, KpiTile } from "../components/blocks";
import { QuarterChart } from "../components/charts";
import { DatasetSelect } from "../components/Shell";
import { EmptyState, ErrorState, LoadingBlock, PageHeader, Panel } from "../components/ui";
import { api } from "../lib/api";
import { useDataset } from "../lib/dataset";
import { auditText, dateTime, percent } from "../lib/format";
import { usePending } from "../lib/pending";
import { useApi } from "../lib/useApi";

function AgentStat({ label, value }: { label: string; value: string | number }) {
  return (
    <div>
      <dt className="text-[13px] text-ink-2">{label}</dt>
      <dd className="tabular mt-1 text-[22px] leading-tight font-bold tracking-[-0.02em]">{value}</dd>
    </div>
  );
}

function AgentCard() {
  const agent = useApi("agent-summary", api.agentSummary);
  const data = agent.data;
  const last = data?.last_agent_action;
  return (
    <Panel
      className="mt-6"
      title="Agent"
      description="Explains flags and proposes actions, across DataCo and onboarded datasets. Nothing takes effect without a named human."
      actions={
        <div className="flex shrink-0 flex-wrap justify-end gap-2">
          <Link to="/investigations" className="glass-pill px-3.5 py-1.5 text-[13px]">
            Investigations <ArrowRight aria-hidden size={14} />
          </Link>
          <Link to="/approvals" className="glass-pill px-3.5 py-1.5 text-[13px]">
            Approvals <ArrowRight aria-hidden size={14} />
          </Link>
        </div>
      }
    >
      {agent.error ? (
        <ErrorState error={agent.error} onRetry={agent.reload} />
      ) : !data ? (
        <LoadingBlock rows={2} label="Loading agent summary" />
      ) : (
        <>
          <dl className="grid gap-5 sm:grid-cols-3">
            <AgentStat label="Investigations done" value={data.investigations} />
            <AgentStat
              label="Grounded rate"
              value={data.grounded_rate == null ? "–" : percent(data.grounded_rate, 1)}
            />
            <AgentStat label="Proposals awaiting approval" value={data.pending_approvals} />
          </dl>
          <p className="mt-4 flex flex-wrap items-center gap-x-4 gap-y-1 border-t border-line pt-4 text-[13px] text-ink-2">
            <span className="inline-flex items-center gap-1.5">
              <Bot aria-hidden size={15} className="text-accent" />
              Last agent action:{" "}
              <span className="font-medium text-ink">
                {last ? `${auditText(last.event)} · ${dateTime(last.ts)}` : "none yet"}
              </span>
            </span>
            <span>
              Model <span className="font-medium text-ink">{data.model}</span>
            </span>
          </p>
        </>
      )}
    </Panel>
  );
}

export function OverviewPage() {
  const { selected, selectedName, isDataco } = useDataset();
  const flags = useApi(`flags:${selected}`, () => api.flags({ dataset: selected }));
  const audit = useApi("audit", api.audit);
  const evaluation = useApi("evaluation", api.evaluation);
  const { pending, loading: pendingLoading } = usePending();

  const items = flags.data?.items ?? [];
  const latest = items.map((flag) => flag.quarter).sort().at(-1);
  const inLatest = items.filter((flag) => flag.quarter === latest).length;
  const high = items.filter((flag) => flag.new_tier === "high").length;
  const grounded = evaluation.data?.metrics.grounded_rate;

  return (
    <>
      <PageHeader
        title="Overview"
        meta={
          flags.data
            ? `${isDataco ? "" : `${selectedName} · `}${items.length} risk flags across ${new Set(items.map((flag) => flag.quarter)).size} quarters · latest ${latest ?? "–"}`
            : "Supplier risk at a glance"
        }
        actions={
          <>
            <DatasetSelect className="w-[220px] lg:hidden" />
            <Link to="/datasets/upload" className="glass-pill pill-accent px-4 py-2 text-sm whitespace-nowrap">
              <Upload aria-hidden size={16} /> New dataset
            </Link>
          </>
        }
      />

      <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-4">
        <KpiTile
          label={latest ? `Flags in ${latest}` : "Flags this period"}
          value={flags.data ? inLatest : undefined}
          loading={flags.loading}
          note="Latest flagged quarter"
        />
        <KpiTile
          label="High-tier flags"
          value={flags.data ? high : undefined}
          loading={flags.loading}
          note={flags.data ? `of ${items.length} flags` : undefined}
        />
        <KpiTile
          label="Pending approvals"
          value={pending}
          loading={pendingLoading}
          note={pending ? "Waiting for a reviewer" : "Queue is clear"}
          action={
            <Link to="/approvals" className="glass-pill pill-accent shrink-0 px-3.5 py-1.5 text-[13px] whitespace-nowrap">
              Review approvals <ArrowRight aria-hidden size={14} />
            </Link>
          }
        />
        <KpiTile
          label="Grounded rate"
          value={grounded == null ? undefined : grounded * 100}
          decimals={1}
          suffix="%"
          loading={evaluation.loading}
          note={
            evaluation.data
              ? `Latest evaluation · ${evaluation.data.case_count} cases`
              : evaluation.error
                ? "No evaluation report yet"
                : undefined
          }
        />
      </div>

      <AgentCard />

      <div className="mt-6 grid gap-6 xl:grid-cols-3">
        <Panel
          className="flex flex-col xl:col-span-2"
          title="Flags by quarter"
          description="New tier of each supplier-quarter flag."
          actions={
            <Link to="/flags" className="glass-pill px-3.5 py-1.5 text-[13px]">
              All flags <ArrowRight aria-hidden size={14} />
            </Link>
          }
        >
          {flags.error ? (
            <ErrorState error={flags.error} onRetry={flags.reload} />
          ) : flags.loading ? (
            <LoadingBlock rows={6} label="Loading chart" />
          ) : items.length ? (
            <QuarterChart flags={items} />
          ) : (
            <EmptyState title="No flags yet">
              {isDataco ? "Run the pipeline to score a quarter." : "No entity's tier rose and held in this dataset."}
            </EmptyState>
          )}
        </Panel>

        <Panel
          title="Recent activity"
          description="From the hash-chained audit log."
          actions={
            <Link to="/pipeline" className="glass-pill px-3.5 py-1.5 text-[13px]">
              Audit <ArrowRight aria-hidden size={14} />
            </Link>
          }
        >
          {audit.error ? (
            <ErrorState error={audit.error} onRetry={audit.reload} />
          ) : audit.loading ? (
            <LoadingBlock rows={6} label="Loading activity" />
          ) : audit.data?.entries.length ? (
            <ActivityFeed entries={audit.data.entries.slice(0, 6)} compact />
          ) : (
            <EmptyState title="No activity yet">
              Investigations and decisions appear here as they are recorded.
            </EmptyState>
          )}
        </Panel>
      </div>
    </>
  );
}
