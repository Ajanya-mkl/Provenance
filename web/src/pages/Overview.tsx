import { ArrowRight } from "lucide-react";
import { Link } from "react-router";

import { ActivityFeed, KpiTile } from "../components/blocks";
import { QuarterChart } from "../components/charts";
import { EmptyState, ErrorState, LoadingBlock, PageHeader, Panel } from "../components/ui";
import { api } from "../lib/api";
import { usePending } from "../lib/pending";
import { useApi } from "../lib/useApi";

export function OverviewPage() {
  const flags = useApi("flags", () => api.flags());
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
            ? `${items.length} risk flags across ${new Set(items.map((flag) => flag.quarter)).size} quarters · latest ${latest ?? "–"}`
            : "Supplier risk at a glance"
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
            <EmptyState title="No flags yet">Run the pipeline to score a quarter.</EmptyState>
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
