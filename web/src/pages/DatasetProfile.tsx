import { ArrowRight, KeyRound } from "lucide-react";
import { Link, useParams } from "react-router";

import { DatasetFlow, SeverityBadge, StatusChip } from "../components/datasets";
import { ErrorState, LoadingBlock, PageHeader, Panel } from "../components/ui";
import { useDatasetDetail } from "../lib/datasetUi";
import { date } from "../lib/format";
import type { DatasetColumn, DatasetDetail } from "../lib/types";
import { GateRules } from "./Datasets";

function range(column: DatasetColumn): string {
  if (column.min_value == null) return "–";
  if (column.inferred_type === "string") return `${column.min_value} … ${column.max_value}`;
  return `${column.min_value} – ${column.max_value}`;
}

function Flags({ column }: { column: DatasetColumn }) {
  const flags = [
    column.is_candidate_key && "candidate key",
    column.is_mixed_type && "mixed types",
    column.is_mostly_null && "mostly empty",
  ].filter(Boolean) as string[];
  if (!flags.length) return <span className="text-ink-3">–</span>;
  return (
    <span className="flex flex-wrap gap-1">
      {flags.map((flag) => (
        <span
          key={flag}
          className={`inline-flex items-center gap-1 rounded-full px-2 py-px text-[12px] font-medium ${
            flag === "candidate key" ? "bg-[color-mix(in_srgb,var(--ink)_8%,transparent)] text-ink-2" : "bg-[color-mix(in_srgb,var(--tier-medium)_16%,transparent)] text-ink"
          }`}
        >
          {flag === "candidate key" && <KeyRound aria-hidden size={12} />}
          {flag}
        </span>
      ))}
    </span>
  );
}

export function ColumnTable({ columns, compact = false }: { columns: DatasetColumn[]; compact?: boolean }) {
  return (
    <div className="overflow-x-auto">
      <table className="tabular w-full border-collapse text-left text-[13px]">
        <caption className="sr-only">Profiled columns</caption>
        <thead>
          <tr className="border-b border-line text-ink-3">
            <th scope="col" className="py-2 pr-4 font-medium">Column</th>
            <th scope="col" className="py-2 pr-4 font-medium">Type</th>
            <th scope="col" className="py-2 pr-4 text-right font-medium">Empty</th>
            <th scope="col" className="py-2 pr-4 text-right font-medium">Distinct</th>
            {!compact && <th scope="col" className="py-2 pr-4 font-medium">Range</th>}
            {!compact && <th scope="col" className="py-2 pr-4 font-medium">Date format</th>}
            {!compact && <th scope="col" className="py-2 font-medium">Notes</th>}
          </tr>
        </thead>
        <tbody>
          {columns.map((column) => (
            <tr key={column.column_name} className="border-b border-line last:border-0">
              <th scope="row" className="py-2 pr-4 font-semibold text-ink">{column.column_name}</th>
              <td className="py-2 pr-4 text-ink-2">{column.inferred_type}</td>
              <td className="py-2 pr-4 text-right text-ink-2">{column.null_pct}%</td>
              <td className="py-2 pr-4 text-right text-ink-2">{column.distinct_count.toLocaleString()}</td>
              {!compact && <td className="max-w-[22ch] truncate py-2 pr-4 text-ink-2" title={range(column)}>{range(column)}</td>}
              {!compact && <td className="py-2 pr-4 font-mono text-[12px] text-ink-2">{column.date_format ?? "–"}</td>}
              {!compact && <td className="py-2"><Flags column={column} /></td>}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

export function DatasetHeader({ detail, title }: { detail: DatasetDetail | undefined; title: string }) {
  return (
    <PageHeader
      title={detail ? `${title}: ${detail.name}` : title}
      meta={
        detail ? (
          <span className="inline-flex flex-wrap items-center gap-3">
            <StatusChip status={detail.display_status} />
            <span>
              {detail.row_count?.toLocaleString() ?? "–"} rows × {detail.column_count ?? "–"} columns · uploaded{" "}
              {date(detail.created_at)}
            </span>
          </span>
        ) : undefined
      }
    />
  );
}

export function ProfilePage() {
  const { id = "" } = useParams();
  const detail = useDatasetDetail(id);
  const data = detail.data;
  return (
    <>
      <DatasetHeader detail={data} title="Profile" />
      <DatasetFlow datasetId={id} status={data?.display_status} />
      {detail.error ? (
        <ErrorState error={detail.error} onRetry={detail.reload} />
      ) : !data ? (
        <LoadingBlock rows={8} label="Loading profile" />
      ) : (
        <div className="space-y-6">
          {data.status === "quarantined" && data.gate_report && (
            <Panel title="Why it was quarantined" description={data.status_reason ?? "A hard rule failed."}>
              <GateRules rules={data.gate_report.results} onlyFailures />
            </Panel>
          )}
          <Panel
            title="Columns"
            description={`${data.encoding ?? "–"}, delimiter “${data.delimiter ?? "–"}”. Types come from at least 80% of non-empty values.`}
            actions={
              data.status === "gated" ? (
                <Link to={`/datasets/${id}/mapping`} className="glass-pill pill-accent shrink-0 px-4 py-2 text-sm">
                  Review mapping <ArrowRight aria-hidden size={15} />
                </Link>
              ) : undefined
            }
          >
            <ColumnTable columns={data.columns} />
            {Boolean(data.profile_warnings?.length) && (
              <ul className="mt-4 list-disc space-y-1 pl-5 text-[13px] text-ink-2">
                {data.profile_warnings!.map((warning) => (
                  <li key={warning}>{warning}</li>
                ))}
              </ul>
            )}
          </Panel>
          <Panel
            title="Generated quality rules"
            description="Built from this profile. Hard rules quarantine a batch; tolerance rules only warn."
          >
            {data.suite?.length ? (
              <ul className="divide-y divide-line">
                {data.suite.map((rule) => {
                  const result = data.gate_report?.results.find((item) => item.rule_id === rule.rule_id);
                  return (
                    <li key={rule.rule_id} className="flex flex-wrap items-start gap-x-4 gap-y-1 py-3">
                      <div className="min-w-0 flex-1">
                        <p className="text-[14px] font-medium">
                          {rule.column && <span className="text-ink-2">{rule.column}: </span>}
                          {rule.check}
                        </p>
                        <p className="text-[13px] text-ink-3">Why: {rule.why}</p>
                      </div>
                      <SeverityBadge severity={rule.severity} />
                      {result && (
                        <span className={`text-[13px] font-medium ${result.success ? "text-tier-low" : "text-danger"}`}>
                          {result.success ? "Passed" : "Failed"}
                        </span>
                      )}
                    </li>
                  );
                })}
              </ul>
            ) : (
              <p className="text-[14px] text-ink-2">No rules were generated.</p>
            )}
          </Panel>
        </div>
      )}
    </>
  );
}
