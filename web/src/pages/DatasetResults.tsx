import { CircleSlash, Info } from "lucide-react";
import { Link, useParams } from "react-router";

import { KpiTile } from "../components/blocks";
import { EntityScoreChart } from "../components/charts";
import { DatasetFlow } from "../components/datasets";
import { ConfidenceBadge, EmptyState, ErrorState, LoadingBlock, Panel, TierChange } from "../components/ui";
import { api } from "../lib/api";
import { dateTime, factorLabel, percent, score, supplierLabel } from "../lib/format";
import type { DatasetResults, FactorReport } from "../lib/types";
import { useApi } from "../lib/useApi";
import { useDatasetDetail } from "../lib/datasetUi";
import { DatasetHeader } from "./DatasetProfile";

/** Every factor: used with its renormalised weight, or left out and why. */
export function FactorsPanel({ factors }: { factors: FactorReport[] }) {
  const excluded = factors.filter((factor) => !factor.used);
  return (
    <Panel
      title="Factors and weights"
      description={
        excluded.length
          ? `${excluded.length} factor${excluded.length > 1 ? "s" : ""} left out; the remaining weights were renormalised and confidence is low.`
          : "All three factors are used with the flagship's weights."
      }
    >
      <table className="tabular w-full border-collapse text-left text-[14px]">
        <caption className="sr-only">Scoring factors, their weights and exclusions</caption>
        <thead>
          <tr className="border-b border-line text-[13px] text-ink-3">
            <th scope="col" className="py-2 pr-4 font-medium">Factor</th>
            <th scope="col" className="py-2 pr-4 text-right font-medium">Base weight</th>
            <th scope="col" className="py-2 pr-4 text-right font-medium">Weight used</th>
            <th scope="col" className="py-2 font-medium">Status</th>
          </tr>
        </thead>
        <tbody>
          {factors.map((factor) => (
            <tr key={factor.name} className="border-b border-line align-top last:border-0">
              <th scope="row" className="py-2.5 pr-4 font-semibold">{factorLabel(factor.name)}</th>
              <td className="py-2.5 pr-4 text-right text-ink-2">{factor.base_weight.toFixed(2)}</td>
              <td className="py-2.5 pr-4 text-right font-semibold">{factor.weight.toFixed(3)}</td>
              <td className="py-2.5">
                {factor.used ? (
                  <span className="text-[13px] text-ink-2">Used</span>
                ) : (
                  <span className="inline-flex items-start gap-1.5 text-[13px] text-ink">
                    <CircleSlash aria-hidden size={14} className="mt-0.5 shrink-0 text-tier-medium" />
                    <span>
                      <span className="font-semibold">Excluded:</span> {factor.reason}
                    </span>
                  </span>
                )}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </Panel>
  );
}

function Body({ id, results }: { id: string; results: DatasetResults }) {
  const counts = results.counts;
  const country = results.country;
  return (
    <div className="space-y-6">
      <p className="glass-pill w-fit px-4 py-2 text-[14px] font-medium">
        <Info aria-hidden size={16} /> {results.comparability_note}
      </p>
      <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-4">
        <KpiTile label="Entities scored" value={counts.entities} note={`${counts.scores} entity-quarter scores`} />
        <KpiTile label="Quarters scored" value={counts.quarters} note={counts.latest_quarter ? `Latest ${counts.latest_quarter}` : undefined} />
        <KpiTile label="Active flags" value={counts.active_flags} note={`${counts.high_in_latest} high-tier in the latest quarter`} />
        <KpiTile
          label="Rows rejected"
          value={counts.rows_rejected}
          note={`of ${counts.rows_in.toLocaleString()} rows; ${counts.rows_out.toLocaleString()} in silver`}
        />
      </div>

      <div className="grid gap-6 xl:grid-cols-3">
        <Panel className="xl:col-span-2" title="Scores by entity" description="Risk score per quarter; dashed lines are the tier thresholds.">
          <EntityScoreChart scores={results.scores} />
        </Panel>
        <FactorsPanel factors={results.factors} />
      </div>

      <Panel title="Flags" description="Tier rises that held under hysteresis. Each is an event the agent can investigate.">
        {results.flags.length ? (
          <ul>
            {results.flags.map((flag) => (
              <li key={flag.event_id} className="border-b border-line last:border-0">
                <Link
                  to={`/flags/${encodeURIComponent(flag.event_id)}`}
                  className="grid grid-cols-[minmax(0,1.5fr)_80px_minmax(0,1.2fr)_70px_minmax(0,1.3fr)] items-center gap-4 rounded-[12px] px-3 py-3 hover:bg-[color-mix(in_srgb,var(--ink)_6%,transparent)] max-md:grid-cols-[minmax(0,1fr)_auto]"
                >
                  <span className="truncate font-semibold">{supplierLabel(flag.supplier_key, flag.supplier_name)}</span>
                  <span className="tabular text-ink-2 max-md:hidden">{flag.quarter}</span>
                  <span className="max-md:hidden"><TierChange from={flag.old_tier} to={flag.new_tier} /></span>
                  <span className="tabular font-semibold">{score(flag.score)}</span>
                  <span className="min-w-0 max-md:hidden">
                    <span className="block truncate text-[13px] text-ink-2">{factorLabel(flag.top_factor.name)}</span>
                    <ConfidenceBadge confidence={flag.confidence} />
                  </span>
                </Link>
              </li>
            ))}
          </ul>
        ) : (
          <EmptyState title="No flags">No entity's tier rose and held in this dataset.</EmptyState>
        )}
      </Panel>

      <div className="grid gap-6 lg:grid-cols-3">
        <Panel variant="lite" title="Rejected rows" description="Rows that did not parse into the canonical table.">
          {results.rejects.length ? (
            <ul className="space-y-2 text-[14px]">
              {results.rejects.map((reject) => (
                <li key={reject.reason} className="flex justify-between gap-3">
                  <span className="text-ink-2">{reject.reason}</span>
                  <span className="tabular font-semibold">{reject.rows}</span>
                </li>
              ))}
            </ul>
          ) : (
            <p className="text-[14px] text-ink-2">Every row parsed.</p>
          )}
        </Panel>
        <Panel variant="lite" title="Country match" description="Country values mapped to ISO3 codes.">
          {country?.match_rate != null ? (
            <>
              <p className="tabular text-[32px] leading-none font-bold">{percent(country.match_rate, 1)}</p>
              <p className="mt-2 text-[13px] text-ink-2">
                {country.matched_rows?.toLocaleString()} of {country.rows_with_country?.toLocaleString()} rows ·{" "}
                {country.unmatched_distinct} unmatched values
              </p>
              {Boolean(country.top_unmatched?.length) && (
                <p className="mt-1 text-[13px] text-ink-3">
                  Unmatched: {country.top_unmatched!.map((item) => `${item.value} (${item.rows})`).join(", ")}
                </p>
              )}
            </>
          ) : (
            <p className="text-[14px] text-ink-2">No country column: {country?.reason ?? "the mapping has none"}.</p>
          )}
          {country?.match_rate != null && country.reason && <p className="mt-2 text-[13px] text-ink-3">{country.reason}.</p>}
        </Panel>
        <Panel variant="lite" title="Rules" description="Frozen at the first scoring; reused until refrozen.">
          <dl className="space-y-1.5 text-[14px]">
            <div className="flex justify-between gap-3"><dt className="text-ink-2">Rules version</dt><dd className="font-mono font-semibold">{results.rules_version}</dd></div>
            <div className="flex justify-between gap-3"><dt className="text-ink-2">Bounds</dt><dd>{results.bounds_mode === "dataset" ? "5th–95th percentile of this dataset" : "Flagship rules.yaml"}</dd></div>
            {Object.entries(results.bounds).map(([name, limits]) => (
              <div key={name} className="flex justify-between gap-3">
                <dt className="text-ink-2">{factorLabel(name)}</dt>
                <dd className="tabular">{limits.min} – {limits.max}</dd>
              </div>
            ))}
            {results.last_run && (
              <div className="flex justify-between gap-3 pt-1"><dt className="text-ink-2">Last run</dt><dd>{dateTime(results.last_run.finished_at)}</dd></div>
            )}
          </dl>
        </Panel>
      </div>
      <p className="text-[13px] text-ink-3">
        Open this dataset in Overview and Flags with the dataset selector in the sidebar.{" "}
        <Link to={`/datasets/${id}/run`} className="underline">Run again</Link> to rebuild with the same frozen bounds.
      </p>
    </div>
  );
}

export function ResultsPage() {
  const { id = "" } = useParams();
  const detail = useDatasetDetail(id);
  const results = useApi(`results:${id}`, () => api.results(id));
  return (
    <>
      <DatasetHeader detail={detail.data} title="Results" />
      <DatasetFlow datasetId={id} status={detail.data?.display_status} />
      {results.error ? (
        results.error.status === 404 ? (
          <EmptyState title="Not scored yet">
            <Link to={`/datasets/${id}/run`} className="underline">Run scoring</Link> to see results.
          </EmptyState>
        ) : (
          <ErrorState error={results.error} onRetry={results.reload} />
        )
      ) : results.data ? (
        <Body id={id} results={results.data} />
      ) : (
        <LoadingBlock rows={8} label="Loading results" />
      )}
    </>
  );
}
