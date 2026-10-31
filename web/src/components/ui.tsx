import {
  CircleAlert,
  Hourglass,
  OctagonAlert,
  RefreshCw,
  ShieldCheck,
  TriangleAlert,
} from "lucide-react";
import type { ReactNode } from "react";
import { useId, useState } from "react";

import type { ApiError } from "../lib/api";
import type { Tier } from "../lib/types";

const TIER_META: Record<Tier, { label: string; Icon: typeof ShieldCheck; tone: string }> = {
  low: { label: "Low", Icon: ShieldCheck, tone: "text-tier-low" },
  medium: { label: "Medium", Icon: TriangleAlert, tone: "text-tier-medium" },
  high: { label: "High", Icon: OctagonAlert, tone: "text-tier-high" },
};

/** Tier is always icon + word + colour, never colour alone. */
export function TierBadge({ tier, size = "md" }: { tier: Tier | null; size?: "sm" | "md" }) {
  if (!tier) {
    return <span className="text-sm text-ink-3">New</span>;
  }
  const { label, Icon, tone } = TIER_META[tier];
  const small = size === "sm";
  return (
    <span
      className={`inline-flex items-center gap-1.5 rounded-full font-semibold ${tone} ${
        small ? "text-[13px]" : "text-sm"
      }`}
    >
      <Icon aria-hidden size={small ? 14 : 16} strokeWidth={2.25} />
      {label}
    </span>
  );
}

export function TierChange({ from, to }: { from: Tier | null; to: Tier }) {
  return (
    <span className="inline-flex items-center gap-2">
      <TierBadge tier={from} size="sm" />
      <span aria-label="to" className="text-ink-3">
        →
      </span>
      <TierBadge tier={to} size="sm" />
    </span>
  );
}

export function ConfidenceBadge({ confidence }: { confidence: string }) {
  const low = confidence === "low";
  return (
    <span
      className={`inline-flex items-center gap-1 text-[13px] font-medium ${
        low ? "text-tier-medium" : "text-ink-2"
      }`}
    >
      {low && <CircleAlert aria-hidden size={14} strokeWidth={2.25} />}
      {low ? "Low confidence" : "Full confidence"}
    </span>
  );
}

export function PageHeader({
  title,
  meta,
  actions,
}: {
  title: string;
  meta?: ReactNode;
  actions?: ReactNode;
}) {
  return (
    <header className="mb-8 flex flex-wrap items-end justify-between gap-4">
      <div>
        <h1 className="text-[34px] leading-[1.1] font-bold tracking-[-0.03em] text-ink">{title}</h1>
        {meta && <p className="mt-2 text-[15px] text-ink-2">{meta}</p>}
      </div>
      {actions && <div className="flex items-center gap-2">{actions}</div>}
    </header>
  );
}

export function Panel({
  title,
  description,
  actions,
  children,
  className = "",
  variant = "glass",
}: {
  title?: string;
  description?: ReactNode;
  actions?: ReactNode;
  children: ReactNode;
  className?: string;
  /** glass blurs; lite keeps the look without blur; flat sits inside another surface. */
  variant?: "glass" | "lite" | "flat";
}) {
  const headingId = useId();
  return (
    <section
      aria-labelledby={title ? headingId : undefined}
      className={`${
        variant === "flat" ? "border-t border-line pt-6" : `${variant === "lite" ? "glass-lite" : "glass"} p-6`
      } ${className}`}
    >
      {(title || actions) && (
        <div className="mb-5 flex items-start justify-between gap-4">
          <div>
            {title && (
              <h2 id={headingId} className="text-[17px] font-semibold text-ink">
                {title}
              </h2>
            )}
            {description && <p className="mt-1 text-sm text-ink-2">{description}</p>}
          </div>
          {actions}
        </div>
      )}
      {children}
    </section>
  );
}

export function Skeleton({ className = "" }: { className?: string }) {
  return <div aria-hidden className={`skeleton ${className}`} />;
}

export function LoadingBlock({ rows = 3, label }: { rows?: number; label: string }) {
  return (
    <div role="status" aria-label={label} className="space-y-3">
      {Array.from({ length: rows }, (_, index) => (
        <Skeleton key={index} className={`h-5 ${index % 3 === 2 ? "w-2/3" : "w-full"}`} />
      ))}
    </div>
  );
}

export function EmptyState({
  title,
  children,
  icon,
}: {
  title: string;
  children?: ReactNode;
  icon?: ReactNode;
}) {
  return (
    <div className="flex flex-col items-center px-6 py-10 text-center">
      {icon && <div className="mb-3 text-ink-3">{icon}</div>}
      <p className="text-[15px] font-semibold text-ink">{title}</p>
      {children && <div className="mt-1 max-w-[46ch] text-sm text-ink-2">{children}</div>}
    </div>
  );
}

export function ErrorState({ error, onRetry }: { error: ApiError; onRetry?: () => void }) {
  const unreachable = error.status === 0;
  if (error.status === 409 && error.code === "locked") {
    return (
      <div role="status" className="flex flex-col items-center px-6 py-10 text-center">
        <Hourglass aria-hidden className="mb-3 text-tier-medium" size={22} />
        <p className="text-[15px] font-semibold text-ink">Pipeline running</p>
        <p className="mt-1 max-w-[52ch] text-sm text-ink-2">
          This has not been loaded yet and the warehouse is busy. It will load when the run
          finishes.
        </p>
        {onRetry && (
          <button type="button" onClick={onRetry} className="glass-pill mt-4 px-4 py-2 text-sm">
            <RefreshCw aria-hidden size={15} /> Check again
          </button>
        )}
      </div>
    );
  }
  return (
    <div role="alert" className="flex flex-col items-center px-6 py-10 text-center">
      <CircleAlert aria-hidden className="mb-3 text-danger" size={22} />
      <p className="text-[15px] font-semibold text-ink">
        {unreachable ? "Can't reach the API" : "Couldn't load this"}
      </p>
      <p className="mt-1 max-w-[52ch] text-sm text-ink-2">
        {error.message}
        {unreachable && " Or open the console in mock mode with ?mock=1."}
      </p>
      {onRetry && (
        <button type="button" onClick={onRetry} className="glass-pill mt-4 px-4 py-2 text-sm">
          <RefreshCw aria-hidden size={15} /> Try again
        </button>
      )}
    </div>
  );
}

export function Segmented<T extends string>({
  label,
  options,
  value,
  onChange,
}: {
  label: string;
  options: { value: T; label: string; icon?: ReactNode }[];
  value: T;
  onChange: (value: T) => void;
}) {
  const move = (direction: number) => {
    const index = options.findIndex((option) => option.value === value);
    const next = options[(index + direction + options.length) % options.length];
    onChange(next.value);
  };
  return (
    <div
      role="radiogroup"
      aria-label={label}
      className="glass-pill max-w-full overflow-x-auto p-1 [scrollbar-width:none]"
      onKeyDown={(event) => {
        if (event.key === "ArrowRight" || event.key === "ArrowDown") {
          event.preventDefault();
          move(1);
        } else if (event.key === "ArrowLeft" || event.key === "ArrowUp") {
          event.preventDefault();
          move(-1);
        }
      }}
    >
      {options.map((option) => {
        const active = option.value === value;
        return (
          <button
            key={option.value}
            type="button"
            role="radio"
            aria-checked={active}
            tabIndex={active ? 0 : -1}
            onClick={() => onChange(option.value)}
            className={`inline-flex shrink-0 items-center gap-1.5 rounded-full px-3.5 py-1.5 text-sm font-medium whitespace-nowrap transition-colors duration-200 ${
              active ? "bg-ink text-[var(--ground)] shadow-sm" : "text-ink-2 hover:text-ink"
            }`}
          >
            {option.icon}
            {option.label}
          </button>
        );
      })}
    </div>
  );
}

/** A chart's data, as a real table, behind a disclosure. */
export function DataTable({
  caption,
  columns,
  rows,
}: {
  caption: string;
  columns: string[];
  rows: (string | number)[][];
}) {
  const [open, setOpen] = useState(false);
  return (
    <details
      className="mt-4 text-sm"
      open={open}
      onToggle={(event) => setOpen((event.target as HTMLDetailsElement).open)}
    >
      <summary className="cursor-pointer text-ink-2 hover:text-ink">Show data table</summary>
      <table className="tabular mt-3 w-full border-collapse text-left">
        <caption className="sr-only">{caption}</caption>
        <thead>
          <tr className="border-b border-line text-ink-3">
            {columns.map((column) => (
              <th key={column} scope="col" className="py-2 pr-4 font-medium">
                {column}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {rows.map((row, index) => (
            <tr key={index} className="border-b border-line last:border-0">
              {row.map((cell, cellIndex) => (
                <td key={cellIndex} className="py-2 pr-4 text-ink">
                  {cell}
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </details>
  );
}
