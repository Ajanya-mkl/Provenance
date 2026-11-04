import {
  BadgeCheck,
  Check,
  CircleAlert,
  CircleX,
  FileUp,
  Link2,
  ShieldAlert,
  ShieldCheck,
  TableProperties,
} from "lucide-react";
import type { ReactNode } from "react";
import { NavLink } from "react-router";

import type { DatasetStatus } from "../lib/types";

const STATUS_META: Record<DatasetStatus, { label: string; Icon: typeof Check; tone: string }> = {
  uploaded: { label: "Uploaded", Icon: FileUp, tone: "text-ink-2" },
  profiled: { label: "Profiled", Icon: TableProperties, tone: "text-ink-2" },
  gated: { label: "Gated", Icon: ShieldCheck, tone: "text-ink" },
  quarantined: { label: "Quarantined", Icon: ShieldAlert, tone: "text-danger" },
  failed: { label: "Failed", Icon: CircleX, tone: "text-danger" },
  mapped: { label: "Mapped", Icon: Link2, tone: "text-tier-medium" },
  scored: { label: "Scored", Icon: BadgeCheck, tone: "text-tier-low" },
};

/** Status is icon + word + colour, never colour alone. */
export function StatusChip({ status }: { status: DatasetStatus }) {
  const { label, Icon, tone } = STATUS_META[status] ?? STATUS_META.uploaded;
  return (
    <span
      className={`inline-flex items-center gap-1.5 rounded-full bg-[color-mix(in_srgb,var(--ink)_7%,transparent)] px-2.5 py-0.5 text-[13px] font-semibold ${tone}`}
    >
      <Icon aria-hidden size={14} strokeWidth={2.25} />
      {label}
    </span>
  );
}

/** Hard rules quarantine a batch; tolerance rules only warn. */
export function SeverityBadge({ severity }: { severity: "hard" | "tolerance" }) {
  const hard = severity === "hard";
  return (
    <span
      title={hard ? "A failure quarantines the upload" : "A failure is reported as a warning"}
      className={`inline-flex shrink-0 items-center rounded-full border px-2 py-px text-[12px] font-semibold ${
        hard ? "border-[color-mix(in_srgb,var(--danger)_45%,transparent)] text-danger" : "border-line text-ink-2"
      }`}
    >
      {hard ? "Hard" : "Tolerance"}
    </span>
  );
}

export function ResultMark({ ok, label }: { ok: boolean; label?: string }) {
  return (
    <span className={`inline-flex items-center gap-1 text-[13px] font-medium ${ok ? "text-tier-low" : "text-danger"}`}>
      {ok ? <Check aria-hidden size={14} strokeWidth={2.5} /> : <CircleX aria-hidden size={14} />}
      {label ?? (ok ? "Passed" : "Failed")}
    </span>
  );
}

export function Verdict({ ok, label, reason }: { ok: boolean; label: string; reason: string }) {
  return (
    <div className="flex items-start gap-3">
      <span className={`mt-0.5 ${ok ? "text-tier-low" : "text-tier-medium"}`}>
        {ok ? <BadgeCheck aria-hidden size={18} /> : <CircleAlert aria-hidden size={18} />}
      </span>
      <p className="text-[14px] text-ink-2">
        <span className="font-semibold text-ink">
          {label}: {ok ? "yes" : "no"}
        </span>{" "}
        – {reason}
      </p>
    </div>
  );
}

const FLOW = [
  { key: "profile", label: "Profile", path: "" },
  { key: "mapping", label: "Mapping", path: "/mapping" },
  { key: "run", label: "Run", path: "/run" },
  { key: "results", label: "Results", path: "/results" },
] as const;

/** Upload -> Profile -> Mapping -> Run -> Results, with what each step needs. */
export function DatasetFlow({ datasetId, status }: { datasetId: string; status?: DatasetStatus }) {
  const reachable: Record<string, boolean> = {
    profile: true,
    mapping: status === "gated" || status === "mapped" || status === "scored",
    run: status === "mapped" || status === "scored",
    results: status === "scored",
  };
  return (
    <nav aria-label="Dataset steps" className="mb-8">
      <ol className="glass-pill flex w-fit max-w-full flex-wrap gap-1 p-1">
        <li>
          <span className="inline-flex items-center gap-1.5 rounded-full px-3.5 py-1.5 text-sm font-medium text-ink-3">
            <Check aria-hidden size={14} /> Upload
          </span>
        </li>
        {FLOW.map((step, index) =>
          reachable[step.key] ? (
            <li key={step.key}>
              <NavLink
                end
                to={`/datasets/${datasetId}${step.path}`}
                className={({ isActive }) =>
                  `inline-flex items-center gap-1.5 rounded-full px-3.5 py-1.5 text-sm font-medium transition-colors ${
                    isActive ? "bg-ink text-[var(--ground)]" : "text-ink-2 hover:text-ink"
                  }`
                }
              >
                <span aria-hidden className="tabular text-[12px] opacity-70">
                  {index + 2}
                </span>
                {step.label}
              </NavLink>
            </li>
          ) : (
            <li key={step.key}>
              <span
                aria-disabled="true"
                title="Not available yet"
                className="inline-flex items-center gap-1.5 rounded-full px-3.5 py-1.5 text-sm font-medium text-ink-3 opacity-60"
              >
                <span aria-hidden className="tabular text-[12px]">
                  {index + 2}
                </span>
                {step.label}
              </span>
            </li>
          ),
        )}
      </ol>
    </nav>
  );
}

export function FieldLabel({ children, htmlFor }: { children: ReactNode; htmlFor?: string }) {
  return (
    <label htmlFor={htmlFor} className="mb-1.5 block text-[13px] font-medium text-ink-2">
      {children}
    </label>
  );
}
