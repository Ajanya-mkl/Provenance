import {
  BadgeCheck,
  Bot,
  CircleX,
  FileCheck2,
  FileWarning,
  ShieldAlert,
  Sparkles,
  ThumbsDown,
  Zap,
} from "lucide-react";
import type { ReactNode } from "react";
import { Link } from "react-router";

import { AGENT_ACTOR, auditText, dateTime, date, quarterFromIso, shortId } from "../lib/format";
import { useSupplierName } from "../lib/names";
import type { AuditEntry, SupplierHistory } from "../lib/types";
import { useCountUp } from "../lib/useCountUp";

export function KpiTile({
  label,
  value,
  decimals = 0,
  suffix = "",
  note,
  action,
  loading,
}: {
  label: string;
  value: number | undefined;
  decimals?: number;
  suffix?: string;
  note?: ReactNode;
  action?: ReactNode;
  loading?: boolean;
}) {
  const shown = useCountUp(value, decimals);
  return (
    <div className="glass-lite lift flex min-h-[148px] flex-col p-5">
      <p className="text-[13px] font-medium text-ink-2">{label}</p>
      {loading ? (
        <div aria-hidden className="skeleton mt-3 h-10 w-24" />
      ) : (
        <p className="tabular mt-2 text-[40px] leading-none font-bold tracking-[-0.03em]">
          {shown}
          {value !== undefined && suffix && <span className="text-[24px] font-semibold">{suffix}</span>}
        </p>
      )}
      <div className="mt-auto flex flex-col items-start gap-3 pt-3">
        {note && <p className="text-[13px] text-ink-3">{note}</p>}
        {action}
      </div>
    </div>
  );
}

const AUDIT_ICON: Record<string, typeof Zap> = {
  investigation_proposed: FileCheck2,
  investigation_rejected_ungrounded: FileWarning,
  investigation_no_report: FileWarning,
  action_proposed: Sparkles,
  action_type_rejected: ShieldAlert,
  action_approved: BadgeCheck,
  action_applied: Zap,
  action_rejected: ThumbsDown,
  apply_refused: CircleX,
};

function auditSubject(
  detail: Record<string, unknown>,
  nameOf: (key: string) => string,
): string | null {
  const event = typeof detail.event_id === "string" ? detail.event_id : null;
  if (event) {
    // A dataset event is 'ds_<id>:risk_flag:...'; the parts after the prefix are the same.
    const [, , quarter, supplier] = event.replace(/^ds_[0-9a-f]{12}:/, "").split(":");
    if (supplier) return `${nameOf(supplier)} · ${quarterFromIso(quarter)}`;
    return event;
  }
  return typeof detail.action_id === "string" ? `Action ${shortId(detail.action_id)}` : null;
}

/** A plain list (no blur inside); newest first. */
export function ActivityFeed({ entries, compact = false }: { entries: AuditEntry[]; compact?: boolean }) {
  const nameOf = useSupplierName();
  return (
    <ol className="flex flex-col">
      {entries.map((entry) => {
        const agent = entry.actor === AGENT_ACTOR;
        const Icon = agent ? Bot : (AUDIT_ICON[entry.event] ?? Zap);
        const subject = auditSubject(entry.detail, nameOf);
        const warn = /rejected|refused/.test(entry.event);
        return (
          <li key={entry.seq} className="flex gap-3 border-b border-line py-3 last:border-0">
            <span
              aria-hidden
              className={`mt-0.5 flex size-8 shrink-0 items-center justify-center rounded-full ${
                warn
                  ? "bg-danger/15 text-danger"
                  : agent
                    ? "bg-accent/15 text-accent"
                    : "bg-[color-mix(in_srgb,var(--ink)_9%,transparent)] text-ink-2"
              }`}
            >
              <Icon size={15} />
            </span>
            <div className="min-w-0 flex-1">
              <p className="text-[14px] font-medium text-ink">{auditText(entry.event)}</p>
              <p className="text-[13px] text-ink-3">
                {subject && <span className="text-ink-2">{subject} · </span>}
                {agent ? (
                  <span className="inline-flex items-center gap-1 font-semibold text-ink-2">Agent</span>
                ) : (
                  entry.actor
                )}{" "}
                · <span className="whitespace-nowrap">{dateTime(entry.ts)}</span>
                {!compact && <span className="tabular whitespace-nowrap"> · #{entry.seq}</span>}
              </p>
            </div>
          </li>
        );
      })}
    </ol>
  );
}

/** SCD2 versions as a vertical timeline, newest at the top. */
export function Timeline({ history }: { history: SupplierHistory }) {
  return (
    <ol className="relative ml-2 border-l border-line">
      {history.versions.map((version) => (
        <li key={version.version} className="relative pb-6 pl-6 last:pb-0">
          <span
            aria-hidden
            className={`absolute top-1 -left-[7px] size-3.5 rounded-full border-2 ${
              version.is_current ? "border-accent bg-accent" : "border-ink-3 bg-[var(--ground)]"
            }`}
          />
          <p className="text-[14px] font-semibold">
            Version {version.version}
            {version.is_current && (
              <span className="ml-2 rounded-full bg-accent/20 px-2 py-0.5 text-[12px] font-medium text-ink">Current</span>
            )}
          </p>
          <p className="tabular text-[13px] text-ink-2">
            {date(version.effective_from)} → {version.effective_to ? date(version.effective_to) : "now"}
          </p>
          <p className="mt-1 text-[13px] text-ink-3">
            Delivery band <span className="font-medium text-ink-2">{version.delivery_band}</span> · Volume band{" "}
            <span className="font-medium text-ink-2">{version.volume_band}</span>
          </p>
        </li>
      ))}
    </ol>
  );
}

export function EventLink({ eventId, children }: { eventId: string; children: ReactNode }) {
  return (
    <Link to={`/investigations/${encodeURIComponent(eventId)}`} className="underline decoration-line hover:decoration-ink">
      {children}
    </Link>
  );
}
