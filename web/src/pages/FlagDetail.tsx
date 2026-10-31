import { ArrowUpRight, FileSearch } from "lucide-react";
import { Link, useParams } from "react-router";

import { Timeline } from "../components/blocks";
import { FactorStack, MarketMixChart, ScoreRing } from "../components/charts";
import {
  ConfidenceBadge,
  EmptyState,
  ErrorState,
  LoadingBlock,
  PageHeader,
  Panel,
  Skeleton,
  TierChange,
} from "../components/ui";
import { api } from "../lib/api";
import { FACTOR_PLAIN, factorLabel, score, signed, supplierLabel } from "../lib/format";
import type { FlagDetail } from "../lib/types";
import { useApi } from "../lib/useApi";

/** The pipeline's top factor in plain words, honest about the direction it moved. */
function TopFactorSentence({ flag }: { flag: FlagDetail }) {
  const factor = flag.top_factor;
  const change = factor.weighted_contribution_change;
  const moved =
    change == null
      ? null
      : change >= 0
        ? `its weighted contribution rose by ${signed(change)}`
        : `its weighted contribution fell by ${score(Math.abs(change))}, so it is the factor that changed most rather than the one that pushed the score up`;
  return (
    <p className="max-w-[65ch] text-[17px] leading-relaxed text-ink">
      The pipeline names <strong className="font-semibold">{factorLabel(factor.name)}</strong> as the
      top factor{FACTOR_PLAIN[factor.name] ? `: ${FACTOR_PLAIN[factor.name]}` : ""}. It contributes{" "}
      <span className="tabular font-semibold">{score(factor.weighted_contribution)}</span> of the{" "}
      <span className="tabular font-semibold">{score(flag.score)}</span> score
      {moved ? `, and ${moved}` : ""}.
    </p>
  );
}

export function FlagDetailBody({ eventId, wide = false }: { eventId: string; wide?: boolean }) {
  const flag = useApi(`flag:${eventId}`, () => api.flag(eventId));
  const key = flag.data?.supplier_key;
  const quarter = flag.data?.quarter;
  const history = useApi(key ? `history:${key}` : null, () => api.history(key!));
  const breakdown = useApi(key ? `score:${key}:${quarter}` : null, () => api.score(key!, quarter!));
  const markets = useApi(key ? `markets:${key}:${quarter}` : null, () => api.markets(key!, quarter!));

  if (flag.error) return <ErrorState error={flag.error} onRetry={flag.reload} />;
  if (!flag.data) {
    return (
      <div role="status" aria-label="Loading flag" className="space-y-6">
        <Skeleton className="mx-auto size-40 rounded-full" />
        <LoadingBlock rows={4} label="Loading flag" />
      </div>
    );
  }
  const data = flag.data;
  return (
    <div className="space-y-6">
      <div className={`flex flex-col gap-6 ${wide ? "md:flex-row md:items-center" : "sm:flex-row sm:items-center"}`}>
        <ScoreRing value={data.score} tier={data.new_tier} />
        <div className="space-y-3">
          <div className="flex flex-wrap items-center gap-x-4 gap-y-2">
            <TierChange from={data.old_tier} to={data.new_tier} />
            <span className="text-[13px] text-ink-3">{data.quarter}</span>
            <ConfidenceBadge confidence={data.confidence} />
          </div>
          <TopFactorSentence flag={data} />
          <Link
            to={`/investigations/${encodeURIComponent(eventId)}`}
            className="glass-pill px-4 py-2 text-sm font-medium"
          >
            <FileSearch aria-hidden size={16} /> Agent investigation
          </Link>
        </div>
      </div>

      <div className={`grid gap-6 ${wide ? "xl:grid-cols-2" : ""}`}>
        <Panel variant={wide ? "lite" : "flat"} title="Score breakdown" description="Weighted factors that add up to the score.">
          {breakdown.error ? (
            <ErrorState error={breakdown.error} onRetry={breakdown.reload} />
          ) : breakdown.data ? (
            <>
              <FactorStack factors={breakdown.data.factors} total={breakdown.data.score} />
              {breakdown.data.previous_scored_quarter && (
                <p className="mt-4 text-[13px] text-ink-3">
                  Previous scored quarter {breakdown.data.previous_scored_quarter.quarter}:{" "}
                  <span className="tabular font-medium text-ink-2">
                    {score(breakdown.data.previous_scored_quarter.score)}
                  </span>{" "}
                  ({breakdown.data.previous_scored_quarter.tier}). Rules {breakdown.data.rules_version}.
                </p>
              )}
            </>
          ) : (
            <LoadingBlock rows={3} label="Loading score breakdown" />
          )}
        </Panel>

        <Panel variant={wide ? "lite" : "flat"} title="Destination markets" description="Share of this supplier's shipments.">
          {markets.error ? (
            <ErrorState error={markets.error} onRetry={markets.reload} />
          ) : markets.data ? (
            markets.data.markets.length ? (
              <MarketMixChart mix={markets.data} />
            ) : (
              <EmptyState title="No shipments this quarter" />
            )
          ) : (
            <LoadingBlock rows={4} label="Loading market mix" />
          )}
        </Panel>

        <Panel variant={wide ? "lite" : "flat"} title="Supplier history" description="SCD2 versions of the supplier's bands.">
          {history.error ? (
            <ErrorState error={history.error} onRetry={history.reload} />
          ) : history.data ? (
            <Timeline history={history.data} />
          ) : (
            <LoadingBlock rows={3} label="Loading history" />
          )}
        </Panel>
      </div>
    </div>
  );
}

export function FlagPage() {
  const { eventId = "" } = useParams();
  const flag = useApi(`flag:${eventId}`, () => api.flag(eventId));
  const name = flag.data ? supplierLabel(flag.data.supplier_key, flag.data.supplier_name) : "Flag";
  return (
    <>
      <PageHeader
        title={name}
        meta={flag.data ? `${flag.data.quarter} · ${flag.data.supplier_key}` : eventId}
        actions={
          <Link to="/flags" className="glass-pill px-4 py-2 text-sm">
            <ArrowUpRight aria-hidden size={16} className="rotate-[-90deg]" /> All flags
          </Link>
        }
      />
      <FlagDetailBody eventId={eventId} wide />
    </>
  );
}
