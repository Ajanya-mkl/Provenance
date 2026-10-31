import { CircleCheck, CircleX, RefreshCw, ShieldCheck, ShieldX, TriangleAlert } from "lucide-react";

import { ActivityFeed } from "../components/blocks";
import { EmptyState, ErrorState, LoadingBlock, PageHeader, Panel } from "../components/ui";
import { api } from "../lib/api";
import { SOURCE_LABELS, dateTime } from "../lib/format";
import type { QualityRow } from "../lib/types";
import { useApi } from "../lib/useApi";

function GateStatus({ row }: { row: QualityRow }) {
  if (!row.passed) {
    return (
      <span className="inline-flex items-center gap-1.5 font-semibold text-danger">
        <CircleX aria-hidden size={16} /> Failed · {row.status}
      </span>
    );
  }
  if (row.warning_count > 0) {
    return (
      <span className="inline-flex items-center gap-1.5 font-semibold text-tier-medium">
        <TriangleAlert aria-hidden size={16} /> Passed with {row.warning_count} warning
        {row.warning_count > 1 ? "s" : ""}
      </span>
    );
  }
  return (
    <span className="inline-flex items-center gap-1.5 font-semibold text-tier-low">
      <CircleCheck aria-hidden size={16} /> Passed
    </span>
  );
}

export function PipelinePage() {
  const quality = useApi("quality", api.quality);
  const audit = useApi("audit", api.audit);

  const rows = quality.data?.items ?? [];
  const sources = [...new Set(rows.map((row) => row.source))];
  const latestBySource = sources.map((source) => rows.find((row) => row.source === source)!);
  const chain = audit.data?.chain;

  return (
    <>
      <PageHeader title="Pipeline" meta="Quality gate results per source, and the audit log's integrity." />

      <div className="grid gap-6 xl:grid-cols-[minmax(0,1fr)_380px]">
        <div className="space-y-6">
          <div className="grid gap-4 md:grid-cols-3">
            {quality.loading
              ? [0, 1, 2].map((index) => (
                  <div key={index} className="glass-lite p-5">
                    <LoadingBlock rows={3} label="Loading source" />
                  </div>
                ))
              : latestBySource.map((row) => (
                  <div key={row.source} className="glass-lite lift p-5">
                    <p className="text-[13px] font-medium text-ink-2">{SOURCE_LABELS[row.source] ?? row.source}</p>
                    <p className="mt-2 text-[15px]">
                      <GateStatus row={row} />
                    </p>
                    <p className="tabular mt-3 text-[13px] text-ink-3">
                      {row.passed_expectations} passed · {row.failed_expectations} failed
                    </p>
                    <p className="text-[12px] text-ink-3">Latest batch {dateTime(row.checked_at)}</p>
                  </div>
                ))}
          </div>

          <Panel title="Quality gate history" description="Every batch the gate checked, newest first.">
            {quality.error ? (
              <ErrorState error={quality.error} onRetry={quality.reload} />
            ) : quality.loading ? (
              <LoadingBlock rows={5} label="Loading quality results" />
            ) : rows.length === 0 ? (
              <EmptyState title="No batches checked yet">Run the ingestion step to validate a batch.</EmptyState>
            ) : (
              <div className="overflow-x-auto">
                <table className="tabular w-full min-w-[620px] border-collapse text-left text-[14px]">
                  <caption className="sr-only">Quality gate results</caption>
                  <thead>
                    <tr className="border-b border-line text-[13px] text-ink-3">
                      <th scope="col" className="py-2 pr-4 font-medium">Source</th>
                      <th scope="col" className="py-2 pr-4 font-medium">Result</th>
                      <th scope="col" className="py-2 pr-4 font-medium">Passed</th>
                      <th scope="col" className="py-2 pr-4 font-medium">Failed</th>
                      <th scope="col" className="py-2 pr-4 font-medium">Batch</th>
                      <th scope="col" className="py-2 font-medium">Checked</th>
                    </tr>
                  </thead>
                  <tbody>
                    {rows.map((row) => (
                      <tr key={`${row.source}-${row.batch_id}`} className="border-b border-line last:border-0">
                        <td className="py-2.5 pr-4 text-ink">{SOURCE_LABELS[row.source] ?? row.source}</td>
                        <td className="py-2.5 pr-4">
                          <GateStatus row={row} />
                        </td>
                        <td className="py-2.5 pr-4 text-ink-2">{row.passed_expectations}</td>
                        <td className={`py-2.5 pr-4 ${row.failed_expectations ? "font-semibold text-danger" : "text-ink-2"}`}>
                          {row.failed_expectations}
                        </td>
                        <td className="py-2.5 pr-4 font-mono text-[12px] text-ink-3" title={row.batch_id}>
                          {row.batch_id.length > 12 ? `${row.batch_id.slice(0, 12)}…` : row.batch_id}
                        </td>
                        <td className="py-2.5 text-ink-2">{dateTime(row.checked_at)}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </Panel>
        </div>

        <div className="space-y-6">
          <Panel
            title="Audit chain"
            actions={
              <button type="button" onClick={audit.reload} className="glass-pill px-3.5 py-1.5 text-[13px]">
                <RefreshCw aria-hidden size={14} /> Verify again
              </button>
            }
          >
            {audit.error ? (
              <ErrorState error={audit.error} onRetry={audit.reload} />
            ) : !chain ? (
              <LoadingBlock rows={2} label="Verifying audit chain" />
            ) : (
              <div aria-live="polite">
                <p
                  className={`flex items-center gap-2 text-[24px] font-bold tracking-[-0.02em] ${
                    chain.status === "intact" ? "text-tier-low" : "text-danger"
                  }`}
                >
                  {chain.status === "intact" ? <ShieldCheck aria-hidden size={26} /> : <ShieldX aria-hidden size={26} />}
                  {chain.status === "intact" ? "Intact" : "Broken"}
                </p>
                <p className="mt-2 text-[14px] text-ink-2">
                  {chain.status === "intact"
                    ? `All ${chain.rows_verified} rows hash-chain to the first entry.`
                    : `Rows ${chain.broken_seqs.join(", ")} no longer match their hashes: the log was edited outside the agent.`}
                </p>
                <p className="mt-1 text-[13px] text-ink-3">Last verified {dateTime(chain.verified_at)}</p>
              </div>
            )}
          </Panel>

          <Panel title="Audit log" description="Newest first.">
            {audit.loading ? (
              <LoadingBlock rows={6} label="Loading audit log" />
            ) : audit.data?.entries.length ? (
              <div className="max-h-[520px] overflow-y-auto pr-1">
                <ActivityFeed entries={audit.data.entries} />
              </div>
            ) : (
              <EmptyState title="Empty audit log">Entries appear once the agent records an investigation.</EmptyState>
            )}
          </Panel>
        </div>
      </div>
    </>
  );
}
