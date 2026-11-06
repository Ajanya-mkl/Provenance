import { ArrowRight, BadgeCheck, Bot, CircleX, FileSearch, Hourglass, LoaderCircle, OctagonX } from "lucide-react";
import { useEffect, useState } from "react";
import { Link } from "react-router";

import { api, ApiError } from "../lib/api";
import { ACTION_LABELS } from "../lib/format";
import { useApprovals } from "../lib/pending";
import type { Investigation } from "../lib/types";
import { useApi } from "../lib/useApi";
import { useRun } from "../lib/useRun";
import { ReceiptScope, ReceiptText } from "./Receipts";
import { LoadingBlock } from "./ui";

export function GroundedBadge({ investigation }: { investigation: Investigation }) {
  const numbers = investigation.report?.summary_spans.filter((span) => span.kind === "number").length ?? 0;
  return (
    <div className="inline-flex items-center gap-2 rounded-full bg-tier-low/15 px-3.5 py-1.5 text-[14px] font-semibold text-tier-low">
      <BadgeCheck aria-hidden size={17} />
      GROUNDED
      <span className="font-normal text-ink-2">
        · every number traces to a tool result{numbers ? ` (${numbers} in the summary)` : ""}
      </span>
    </div>
  );
}

export function NotGroundedBanner({ investigation }: { investigation: Investigation }) {
  return (
    <div
      role="alert"
      className="flex gap-4 rounded-[20px] bg-danger/15 p-5 shadow-[inset_0_0_0_1px_color-mix(in_srgb,var(--danger)_45%,transparent)]"
    >
      <OctagonX aria-hidden size={28} className="shrink-0 text-danger" />
      <div>
        <p className="text-[17px] font-bold tracking-[-0.01em] text-danger">NOT GROUNDED</p>
        <p className="mt-1 max-w-[65ch] text-[15px] text-ink">
          This report contains numbers no tool returned, so it was rejected. Its proposed action is
          hidden and no action was created.
        </p>
        {investigation.ungrounded_numbers.length > 0 && (
          <p className="mt-2 text-[14px] text-ink-2">
            Unsourced:{" "}
            {investigation.ungrounded_numbers.map((number) => (
              <span key={number} className="tabular mr-2 font-semibold text-danger line-through decoration-2">
                {number}
              </span>
            ))}
          </p>
        )}
      </div>
    </div>
  );
}

const STAGE_WORDS: Record<string, string> = {
  investigating: "The agent is reading the flag and calling its tools…",
  recording: "Checking every number against the tools and recording the report…",
};

function GroundedReport({ eventId, investigation }: { eventId: string; investigation: Investigation }) {
  const report = investigation.report;
  const action = investigation.action;
  return (
    <ReceiptScope>
      <div className="space-y-3">
        <GroundedBadge investigation={investigation} />
        {report && (
          <p className="max-w-[68ch] text-[15px] leading-relaxed text-ink">
            <ReceiptText spans={report.summary_spans} />
          </p>
        )}
        {action && (
          <p className="text-[14px] text-ink-2">
            <span className="font-semibold text-ink">
              Proposed: {ACTION_LABELS[action.action_type] ?? action.action_type}.
            </span>{" "}
            {action.status === "proposed"
              ? "Nothing has been applied; it waits for a named human."
              : `Status: ${action.status}.`}
          </p>
        )}
        <div className="flex flex-wrap gap-2">
          <Link to={`/investigations/${encodeURIComponent(eventId)}`} className="glass-pill px-4 py-2 text-sm font-medium">
            <FileSearch aria-hidden size={16} /> Full report
          </Link>
          {action?.status === "proposed" && (
            <Link to="/approvals" className="glass-pill pill-accent px-4 py-2 text-sm">
              Review in approvals <ArrowRight aria-hidden size={15} />
            </Link>
          )}
        </div>
      </div>
    </ReceiptScope>
  );
}

/**
 * The agent on one flag: its grounded report, or a button that starts it in the
 * background and shows live status. The agent only proposes; approving is a
 * separate step that needs a named human.
 */
export function AgentInvestigate({ eventId, resolved = false }: { eventId: string; resolved?: boolean }) {
  const investigation = useApi(`investigation:${eventId}`, () => api.investigation(eventId));
  const approvals = useApprovals();
  const [runId, setRunId] = useState<string | null>(null);
  const [starting, setStarting] = useState(false);
  const [refusal, setRefusal] = useState<ApiError | null>(null);
  const { run, error } = useRun(runId, api.investigationRun);

  const finished = run?.status === "succeeded";
  useEffect(() => {
    if (!finished) return;
    investigation.reload();
    approvals.reload();
    // Once, when the run finishes.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [finished]);

  const start = async () => {
    setStarting(true);
    setRefusal(null);
    try {
      setRunId((await api.startInvestigation(eventId)).run_id);
    } catch (caught) {
      setRefusal(caught instanceof ApiError ? caught : new ApiError(0, String(caught)));
    } finally {
      setStarting(false);
    }
  };

  const data = investigation.data;
  const grounded = Boolean(data && data.status === "proposed" && data.grounded);
  const active = run && (run.status === "queued" || run.status === "running");
  const missing = investigation.error?.status === 404;
  if (!active && !data && !investigation.error) return <LoadingBlock rows={2} label="Loading investigation" />;
  if (grounded && !active && data) return <GroundedReport eventId={eventId} investigation={data} />;

  return (
    <div className="space-y-3">
      {data?.status === "rejected_ungrounded" && !active && <NotGroundedBanner investigation={data} />}
      {data?.status === "no_report" && !active && (
        <p className="text-[14px] text-ink-2">The last run ended without a report, so nothing was proposed.</p>
      )}
      {active ? (
        <p role="status" className="flex items-center gap-2 text-[15px] font-medium text-ink">
          <LoaderCircle aria-hidden size={17} className="animate-spin motion-reduce:animate-none" />
          {STAGE_WORDS[run.stage ?? ""] ?? "Starting the agent…"}
        </p>
      ) : resolved ? (
        <p className="text-[14px] text-ink-2">This flag is resolved, so there is nothing to investigate.</p>
      ) : (
        <>
          {missing && <p className="text-[14px] text-ink-2">The agent has not investigated this flag yet.</p>}
          <button type="button" onClick={start} disabled={starting} className="glass-pill pill-accent px-4 py-2 text-sm">
            <Bot aria-hidden size={16} /> {starting ? "Starting…" : data ? "Investigate again" : "Investigate with agent"}
          </button>
          <p className="text-[13px] text-ink-3">
            The agent only proposes. Anything it suggests waits for a named human in Approvals.
          </p>
        </>
      )}
      {refusal?.code === "busy" && (
        <div role="status" className="flex gap-3 rounded-[14px] bg-[color-mix(in_srgb,var(--tier-medium)_14%,transparent)] p-4 text-[14px]">
          <Hourglass aria-hidden size={18} className="mt-0.5 shrink-0 text-tier-medium" />
          <div>
            <p className="font-semibold">The warehouse is busy</p>
            <p className="text-ink-2">{refusal.message}</p>
          </div>
        </div>
      )}
      {refusal && refusal.code !== "busy" && (
        <p role="alert" className="text-[14px] text-danger">
          {refusal.message}
        </p>
      )}
      {run?.status === "failed" && (
        <p role="alert" className="flex gap-2 text-[14px] text-danger">
          <CircleX aria-hidden size={16} className="mt-0.5 shrink-0" />
          {run.error}
        </p>
      )}
      {run?.status === "succeeded" && run.investigation_status === "no_report" && (
        <p role="alert" className="text-[14px] text-danger">
          The agent ran out of steps without a report, so nothing was proposed.
        </p>
      )}
      {error && !active && <p role="alert" className="text-[14px] text-danger">{error.message}</p>}
    </div>
  );
}
