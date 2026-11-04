import { motion, useReducedMotion } from "framer-motion";
import { useId, useState } from "react";
import {
  Bar,
  BarChart,
  CartesianGrid,
  LabelList,
  Legend,
  Line,
  LineChart,
  ReferenceLine,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import type { TooltipContentProps } from "recharts";

import { factorLabel, percent, score as fmtScore } from "../lib/format";
import type { DatasetScore, FlagSummary, MarketMix, ScoreFactor, Tier } from "../lib/types";
import { DataTable, TierBadge } from "./ui";

const TIER_COLOR: Record<Tier, string> = {
  low: "var(--tier-low)",
  medium: "var(--tier-medium)",
  high: "var(--tier-high)",
};
const FACTOR_ORDER = ["delay_stddev", "late_rate", "country_risk"] as const;
const factorColor = (name: string) => `var(--factor-${name})`;

const axisTick = { fill: "var(--ink-3)", fontSize: 12 };

function ChartTooltip({ active, payload, label }: Partial<TooltipContentProps<number, string>>) {
  if (!active || !payload?.length) return null;
  return (
    <div className="glass-strong rounded-[14px] px-3 py-2 text-[13px] text-ink">
      {label && <p className="mb-1 font-semibold">{label}</p>}
      {payload.map((entry) => (
        <p key={String(entry.dataKey)} className="tabular flex items-center gap-2 text-ink-2">
          <span aria-hidden className="size-2.5 rounded-[3px]" style={{ background: entry.color }} />
          {entry.name}: <span className="font-semibold text-ink">{entry.value}</span>
        </p>
      ))}
    </div>
  );
}

/** Flags by quarter, stacked by tier. Status colours, legend with icon + label. */
export function QuarterChart({ flags }: { flags: FlagSummary[] }) {
  const reduce = useReducedMotion();
  const quarters = [...new Set(flags.map((flag) => flag.quarter))].sort();
  const data = quarters.map((quarter) => {
    const inQuarter = flags.filter((flag) => flag.quarter === quarter);
    return {
      quarter,
      low: inQuarter.filter((flag) => flag.new_tier === "low").length,
      medium: inQuarter.filter((flag) => flag.new_tier === "medium").length,
      high: inQuarter.filter((flag) => flag.new_tier === "high").length,
    };
  });
  const tiers = (["medium", "high", "low"] as Tier[]).filter((tier) =>
    data.some((row) => row[tier] > 0),
  );
  const summary = data.map((row) => `${row.quarter}: ${row.medium + row.high + row.low}`).join(", ");
  return (
    <figure className="flex flex-1 flex-col">
      <div
        role="img"
        aria-label={`Bar chart of risk flags per quarter, stacked by new tier. ${summary}.`}
        className="min-h-[260px] flex-1"
      >
        <ResponsiveContainer width="100%" height="100%">
          <BarChart data={data} margin={{ top: 8, right: 8, bottom: 0, left: -24 }} barCategoryGap="28%">
            <CartesianGrid vertical={false} stroke="var(--line)" />
            <XAxis dataKey="quarter" tick={axisTick} axisLine={false} tickLine={false} />
            <YAxis allowDecimals={false} tick={axisTick} axisLine={false} tickLine={false} />
            <Tooltip
              cursor={{ fill: "color-mix(in srgb, var(--ink) 6%, transparent)" }}
              content={<ChartTooltip />}
            />
            {tiers.map((tier, index) => (
              <Bar
                key={tier}
                dataKey={tier}
                name={tier[0].toUpperCase() + tier.slice(1)}
                stackId="tier"
                fill={TIER_COLOR[tier]}
                stroke="var(--ground)"
                strokeWidth={2}
                radius={index === tiers.length - 1 ? [4, 4, 0, 0] : 0}
                isAnimationActive={!reduce}
                animationDuration={500}
              />
            ))}
          </BarChart>
        </ResponsiveContainer>
      </div>
      <figcaption className="mt-3 flex flex-wrap gap-4">
        {tiers.map((tier) => (
          <span key={tier} className="inline-flex items-center gap-2">
            <span aria-hidden className="size-2.5 rounded-[3px]" style={{ background: TIER_COLOR[tier] }} />
            <TierBadge tier={tier} size="sm" />
          </span>
        ))}
      </figcaption>
      <DataTable
        caption="Risk flags per quarter by tier"
        columns={["Quarter", "Medium", "High", "Total"]}
        rows={data.map((row) => [row.quarter, row.medium, row.high, row.medium + row.high + row.low])}
      />
    </figure>
  );
}

/** The score as a ring, tier stated in words beneath it. */
export function ScoreRing({ value, tier, size = 168 }: { value: number; tier: Tier; size?: number }) {
  const reduce = useReducedMotion();
  const stroke = 12;
  const radius = (size - stroke) / 2;
  const circumference = 2 * Math.PI * radius;
  return (
    <div className="flex flex-col items-center gap-3">
      <div className="relative" style={{ width: size, height: size }}>
        <svg width={size} height={size} viewBox={`0 0 ${size} ${size}`} aria-hidden>
          <circle
            cx={size / 2}
            cy={size / 2}
            r={radius}
            fill="none"
            stroke="color-mix(in srgb, var(--ink) 10%, transparent)"
            strokeWidth={stroke}
          />
          <motion.circle
            cx={size / 2}
            cy={size / 2}
            r={radius}
            fill="none"
            stroke={TIER_COLOR[tier]}
            strokeWidth={stroke}
            strokeLinecap="round"
            strokeDasharray={circumference}
            initial={{ strokeDashoffset: reduce ? circumference * (1 - value) : circumference }}
            animate={{ strokeDashoffset: circumference * (1 - value) }}
            transition={reduce ? { duration: 0 } : { type: "spring", stiffness: 90, damping: 20 }}
            transform={`rotate(-90 ${size / 2} ${size / 2})`}
          />
        </svg>
        <div className="absolute inset-0 flex flex-col items-center justify-center">
          <span className="tabular text-[40px] leading-none font-bold tracking-[-0.03em]">
            {fmtScore(value)}
          </span>
          <span className="mt-1 text-[13px] text-ink-3">risk score of 1</span>
        </div>
      </div>
      <TierBadge tier={tier} />
    </div>
  );
}

/** Weighted contributions to the score as one stacked bar, with tooltips. */
export function FactorStack({ factors, total }: { factors: ScoreFactor[]; total: number }) {
  const reduce = useReducedMotion();
  const [active, setActive] = useState<string | null>(null);
  const tipId = useId();
  const ordered = FACTOR_ORDER.map((name) => factors.find((factor) => factor.factor === name)).filter(
    (factor): factor is ScoreFactor => Boolean(factor),
  );
  const current = ordered.find((factor) => factor.factor === active);
  return (
    <figure>
      <div
        role="group"
        aria-label={`Score ${fmtScore(total)} split by weighted factor`}
        className="relative flex h-9 w-full gap-[2px] overflow-visible rounded-[10px] bg-[color-mix(in_srgb,var(--ink)_8%,transparent)]"
      >
        {ordered.map((factor, index) => (
          <motion.button
            key={factor.factor}
            type="button"
            aria-describedby={active === factor.factor ? tipId : undefined}
            aria-label={`${factorLabel(factor.factor)}: contributes ${fmtScore(factor.weighted_contribution)}`}
            onMouseEnter={() => setActive(factor.factor)}
            onMouseLeave={() => setActive(null)}
            onFocus={() => setActive(factor.factor)}
            onBlur={() => setActive(null)}
            initial={reduce ? false : { scaleX: 0 }}
            animate={{ scaleX: 1 }}
            transition={reduce ? { duration: 0 } : { delay: index * 0.06, type: "spring", stiffness: 160, damping: 24 }}
            style={{
              width: `${factor.weighted_contribution * 100}%`,
              background: factorColor(factor.factor),
              transformOrigin: "left",
              opacity: active && active !== factor.factor ? 0.45 : 1,
            }}
            className={`h-full transition-opacity duration-200 ${index === 0 ? "rounded-l-[10px]" : ""} ${
              index === ordered.length - 1 ? "rounded-r-[4px]" : ""
            }`}
          />
        ))}
        {current && (
          <div
            id={tipId}
            role="tooltip"
            className="glass-strong pointer-events-none absolute bottom-full left-0 z-10 mb-2 rounded-[14px] px-3 py-2 text-[13px]"
          >
            <p className="font-semibold">{factorLabel(current.factor)}</p>
            <p className="tabular text-ink-2">
              raw {current.raw_value} · normalised {current.normalized_value} · weight{" "}
              {current.weight} → <span className="font-semibold text-ink">{fmtScore(current.weighted_contribution)}</span>
            </p>
          </div>
        )}
      </div>
      <figcaption className="mt-4 grid gap-2 sm:grid-cols-3">
        {ordered.map((factor) => (
          <div key={factor.factor} className="flex items-start gap-2">
            <span aria-hidden className="mt-1.5 size-2.5 shrink-0 rounded-[3px]" style={{ background: factorColor(factor.factor) }} />
            <div>
              <p className="text-[13px] text-ink-2">{factorLabel(factor.factor)}</p>
              <p className="tabular text-[15px] font-semibold">
                {fmtScore(factor.weighted_contribution)}
                <span className="ml-1 text-[13px] font-normal text-ink-3">× {factor.weight}</span>
              </p>
            </div>
          </div>
        ))}
      </figcaption>
      <DataTable
        caption="Score factors"
        columns={["Factor", "Raw", "Normalised", "Weight", "Contribution", "Previous raw"]}
        rows={ordered.map((factor) => [
          factorLabel(factor.factor),
          factor.raw_value,
          factor.normalized_value,
          factor.weight,
          factor.weighted_contribution,
          factor.previous_raw_value ?? "–",
        ])}
      />
    </figure>
  );
}

/** Destination market share, this quarter against the previous one. */
export function MarketMixChart({ mix }: { mix: MarketMix }) {
  const reduce = useReducedMotion();
  const data = mix.markets.map((market) => ({
    market: market.market,
    current: Math.round(market.share * 1000) / 10,
    previous: market.previous_share == null ? 0 : Math.round(market.previous_share * 1000) / 10,
  }));
  return (
    <figure>
      <div
        role="img"
        aria-label={`Share of shipments by destination market in ${mix.quarter} against ${mix.previous_quarter}. ${mix.markets
          .map((market) => `${market.market} ${percent(market.share)}`)
          .join(", ")}.`}
        style={{ height: 44 * data.length + 48 }}
      >
        <ResponsiveContainer width="100%" height="100%">
          <BarChart data={data} layout="vertical" margin={{ top: 0, right: 16, bottom: 0, left: 8 }} barGap={2} barCategoryGap="22%">
            <CartesianGrid horizontal={false} stroke="var(--line)" />
            <XAxis type="number" unit="%" domain={[0, 100]} tick={axisTick} axisLine={false} tickLine={false} />
            <YAxis type="category" dataKey="market" width={96} tick={{ ...axisTick, fill: "var(--ink-2)" }} axisLine={false} tickLine={false} />
            <Tooltip cursor={{ fill: "color-mix(in srgb, var(--ink) 6%, transparent)" }} content={<ChartTooltip />} />
            <Legend
              verticalAlign="top"
              height={32}
              iconType="square"
              formatter={(value) => <span className="text-[13px] text-ink-2">{value}</span>}
            />
            <Bar dataKey="current" name={mix.quarter} fill="var(--series-primary)" radius={[0, 4, 4, 0]} isAnimationActive={!reduce} animationDuration={500} />
            <Bar dataKey="previous" name={mix.previous_quarter} fill="var(--series-muted)" radius={[0, 4, 4, 0]} isAnimationActive={!reduce} animationDuration={500} />
          </BarChart>
        </ResponsiveContainer>
      </div>
      <DataTable
        caption="Destination market share"
        columns={["Market", mix.quarter, mix.previous_quarter, "Change"]}
        rows={mix.markets.map((market) => [
          market.market,
          percent(market.share, 1),
          percent(market.previous_share, 1),
          market.share_change == null ? "–" : `${(market.share_change * 100).toFixed(1)} pts`,
        ])}
      />
    </figure>
  );
}

const ENTITY_SLOTS = 5;

/**
 * Each entity's score over its scored quarters, one line per entity (categorical
 * slots in fixed order, by entity name, so a colour follows its entity). More than
 * five entities: the five with the highest latest score are drawn, all are in the
 * table. Tier thresholds are dashed reference lines.
 */
export function EntityScoreChart({
  scores,
  thresholds = { medium: 0.38, high: 0.68 },
}: {
  scores: DatasetScore[];
  thresholds?: { medium: number; high: number };
}) {
  const reduce = useReducedMotion();
  const quarters = [...new Set(scores.map((row) => row.quarter))].sort();
  const names = new Map(scores.map((row) => [row.entity_key, row.entity_name ?? row.entity_key]));
  const latest = new Map<string, number>();
  for (const row of [...scores].sort((a, b) => a.quarter.localeCompare(b.quarter))) latest.set(row.entity_key, row.score);
  const entities = [...names.keys()].sort((a, b) => names.get(a)!.localeCompare(names.get(b)!));
  const drawn = [...entities]
    .sort((a, b) => (latest.get(b) ?? 0) - (latest.get(a) ?? 0))
    .slice(0, ENTITY_SLOTS)
    .sort((a, b) => names.get(a)!.localeCompare(names.get(b)!));
  const data = quarters.map((quarter) => {
    const row: Record<string, number | string> = { quarter };
    for (const score of scores) if (score.quarter === quarter) row[score.entity_key] = Number(score.score.toFixed(3));
    return row;
  });
  const lastQuarter = quarters.at(-1);
  return (
    <figure>
      <div
        role="img"
        aria-label={`Line chart of risk score per quarter for ${drawn.length} entities. ${drawn
          .map((key) => `${names.get(key)}: ${fmtScore(latest.get(key))} in the latest quarter`)
          .join(", ")}.`}
        className="h-[300px]"
      >
        <ResponsiveContainer width="100%" height="100%">
          <LineChart data={data} margin={{ top: 12, right: 112, bottom: 0, left: -20 }}>
            <CartesianGrid vertical={false} stroke="var(--line)" />
            <XAxis dataKey="quarter" tick={axisTick} axisLine={false} tickLine={false} />
            <YAxis domain={[0, 1]} ticks={[0, 0.25, 0.5, 0.75, 1]} tick={axisTick} axisLine={false} tickLine={false} />
            <ReferenceLine y={thresholds.medium} stroke="var(--ink-3)" strokeDasharray="4 4" label={{ value: "medium", position: "insideTopLeft", fill: "var(--ink-3)", fontSize: 11 }} />
            <ReferenceLine y={thresholds.high} stroke="var(--ink-3)" strokeDasharray="4 4" label={{ value: "high", position: "insideTopLeft", fill: "var(--ink-3)", fontSize: 11 }} />
            <Tooltip cursor={{ stroke: "var(--ink-3)", strokeWidth: 1 }} content={<ChartTooltip />} />
            {drawn.map((key, index) => (
              <Line
                key={key}
                type="monotone"
                dataKey={key}
                name={names.get(key)}
                stroke={`var(--entity-${index + 1})`}
                strokeWidth={2}
                dot={{ r: 4, strokeWidth: 2, stroke: "var(--ground)" }}
                activeDot={{ r: 5, strokeWidth: 2, stroke: "var(--ground)" }}
                connectNulls
                isAnimationActive={!reduce}
                animationDuration={500}
              >
                {/* Direct label at the line's end: identity never rests on colour alone. */}
                <LabelList
                  dataKey={key}
                  content={({ x, y, index: point }) =>
                    data[Number(point)]?.quarter === lastQuarter && x != null && y != null ? (
                      <text x={Number(x) + 10} y={Number(y) + 4} fontSize={12} fill="var(--ink-2)">
                        {String(names.get(key)).slice(0, 16)}
                      </text>
                    ) : null
                  }
                />
              </Line>
            ))}
          </LineChart>
        </ResponsiveContainer>
      </div>
      <figcaption className="mt-3 flex flex-wrap gap-4 text-[13px] text-ink-2">
        {drawn.map((key, index) => (
          <span key={key} className="inline-flex items-center gap-2">
            <span aria-hidden className="h-0.5 w-4 rounded-full" style={{ background: `var(--entity-${index + 1})` }} />
            {names.get(key)}
          </span>
        ))}
        {entities.length > drawn.length && <span className="text-ink-3">+{entities.length - drawn.length} more in the table</span>}
      </figcaption>
      <DataTable
        caption="Risk score per entity and quarter"
        columns={["Entity", ...quarters]}
        rows={entities.map((key) => [
          names.get(key)!,
          ...quarters.map((quarter) => {
            const row = scores.find((score) => score.entity_key === key && score.quarter === quarter);
            return row ? `${fmtScore(row.score)} ${row.tier}` : "–";
          }),
        ])}
      />
    </figure>
  );
}
