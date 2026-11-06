import { ArrowRight, Check, CircleX, Hourglass, LoaderCircle, Play } from "lucide-react";
import { useEffect, useState } from "react";
import { Link, useParams, useSearchParams } from "react-router";

import { DatasetFlow } from "../components/datasets";
import { EmptyState, ErrorState, LoadingBlock, Panel } from "../components/ui";
import { api, ApiError } from "../lib/api";
import { dateTime } from "../lib/format";
import type { RunState, StageName, StageStatus } from "../lib/types";
import { useDatasetDetail } from "../lib/datasetUi";
import { useRun } from "../lib/useRun";
import { DatasetHeader } from "./DatasetProfile";

const STAGE_TEXT: Record<StageName, { label: string; what: string }> = {
  silver: { label: "Silver", what: "Parse every row into the canonical shipment table; unparseable rows go to rejects." },
  metrics: { label: "Metrics", what: "Late rate, delay variability and volume per entity and quarter, with the flagship's rules." },
  scoring: { label: "Scoring", what: "Normalise with this dataset's frozen bounds and weight the factors it can provide." },
  flags: { label: "Flags", what: "Tier changes with hysteresis become flags and events for the agent." },
};

function StageIcon({ status }: { status: StageStatus }) {
  if (status === "done") return <Check aria-hidden size={16} strokeWidth={2.5} className="text-tier-low" />;
  if (status === "failed") return <CircleX aria-hidden size={16} className="text-danger" />;
  if (status === "running") return <LoaderCircle aria-hidden size={16} className="animate-spin text-ink motion-reduce:animate-none" />;
  return <span aria-hidden className="size-2 rounded-full bg-[color-mix(in_srgb,var(--ink)_25%,transparent)]" />;
}

const STATUS_WORD: Record<StageStatus, string> = {
  pending: "Waiting",
  running: "Running",
  done: "Done",
  failed: "Failed",
};

export function RunStepper({ run }: { run: RunState }) {
  return (
    <ol aria-label="Run stages" className="space-y-1">
      {run.stages.map((stage, index) => (
        <li
          key={stage.name}
          aria-current={stage.status === "running" ? "step" : undefined}
          className={`flex items-start gap-4 rounded-[14px] px-4 py-3 ${
            stage.status === "running" ? "bg-[color-mix(in_srgb,var(--ink)_7%,transparent)]" : ""
          }`}
        >
          <span className="mt-0.5 flex size-7 shrink-0 items-center justify-center rounded-full border border-line">
            <StageIcon status={stage.status} />
          </span>
          <div className="min-w-0 flex-1">
            <p className="text-[15px] font-semibold">
              <span className="tabular mr-2 text-ink-3">{index + 1}</span>
              {STAGE_TEXT[stage.name].label}
              <span className={`ml-3 text-[13px] font-medium ${stage.status === "failed" ? "text-danger" : "text-ink-3"}`}>
                {STATUS_WORD[stage.status]}
              </span>
            </p>
            <p className="text-[13px] text-ink-2">{STAGE_TEXT[stage.name].what}</p>
          </div>
        </li>
      ))}
    </ol>
  );
}

export function RunPage() {
  const { id = "" } = useParams();
  const [params, setParams] = useSearchParams();
  const runId = params.get("run");
  const detail = useDatasetDetail(id);
  const { run, error } = useRun(runId, api.run);
  const [starting, setStarting] = useState(false);
  const [refusal, setRefusal] = useState<ApiError | null>(null);

  useEffect(() => {
    if (run?.status === "succeeded") detail.reload();
    // Reload the dataset once, when the run finishes.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [run?.status]);

  const start = async () => {
    setStarting(true);
    setRefusal(null);
    try {
      const started = await api.startRun(id);
      setParams({ run: started.run_id });
    } catch (caught) {
      setRefusal(caught instanceof ApiError ? caught : new ApiError(0, String(caught)));
    } finally {
      setStarting(false);
    }
  };

  const status = detail.data?.display_status;
  const runnable = status === "mapped" || status === "scored";
  const active = run && (run.status === "queued" || run.status === "running");
  return (
    <>
      <DatasetHeader detail={detail.data} title="Run" />
      <DatasetFlow datasetId={id} status={status} />
      {detail.error ? (
        <ErrorState error={detail.error} onRetry={detail.reload} />
      ) : !detail.data ? (
        <LoadingBlock rows={4} label="Loading dataset" />
      ) : !runnable && !runId ? (
        <Panel title="Not ready to run">
          <EmptyState title="Approve a mapping first">
            Scoring needs an approved, scoreable mapping.{" "}
            <Link to={`/datasets/${id}/mapping`} className="underline">Review the mapping</Link>.
          </EmptyState>
        </Panel>
      ) : (
        <div className="grid gap-6 xl:grid-cols-[minmax(0,1.3fr)_minmax(0,1fr)]">
          <Panel
            title="Scoring run"
            description="Runs in the background, one writer at a time; the console polls its progress."
            actions={
              <button type="button" onClick={start} disabled={starting || Boolean(active)} className="glass-pill pill-accent shrink-0 px-4 py-2 text-sm">
                <Play aria-hidden size={15} /> {active ? "Running…" : starting ? "Starting…" : run ? "Run again" : "Start run"}
              </button>
            }
          >
            {refusal && refusal.code === "busy" ? (
              <div role="status" className="mb-4 flex gap-3 rounded-[14px] bg-[color-mix(in_srgb,var(--tier-medium)_14%,transparent)] p-4 text-[14px]">
                <Hourglass aria-hidden size={18} className="mt-0.5 shrink-0 text-tier-medium" />
                <div>
                  <p className="font-semibold">The warehouse is busy</p>
                  <p className="text-ink-2">{refusal.message}</p>
                </div>
              </div>
            ) : refusal ? (
              <p role="alert" className="mb-4 text-[14px] text-danger">{refusal.message}</p>
            ) : null}
            {run ? (
              <div aria-live="polite">
                <RunStepper run={run} />
                {run.status === "failed" && (
                  <div role="alert" className="mt-4 flex gap-3 rounded-[14px] bg-[color-mix(in_srgb,var(--danger)_12%,transparent)] p-4 text-[14px]">
                    <CircleX aria-hidden size={18} className="mt-0.5 shrink-0 text-danger" />
                    <div>
                      <p className="font-semibold">The run failed; nothing was changed</p>
                      <p className="text-ink-2">{run.error}</p>
                    </div>
                  </div>
                )}
                {run.status === "succeeded" && (
                  <div className="mt-4 flex flex-wrap items-center justify-between gap-3">
                    <p className="text-[14px] text-tier-low">Finished {dateTime(run.finished_at ?? null)}.</p>
                    <Link to={`/datasets/${id}/results`} className="glass-pill pill-accent px-4 py-2 text-sm">
                      See results <ArrowRight aria-hidden size={15} />
                    </Link>
                  </div>
                )}
              </div>
            ) : error && runId ? (
              <ErrorState error={error} />
            ) : runId ? (
              <LoadingBlock rows={4} label="Loading run" />
            ) : (
              <p className="text-[14px] text-ink-2">
                {status === "scored"
                  ? "This dataset has been scored. Running again rebuilds it with the same frozen bounds and creates no duplicate events."
                  : "Start the run to build silver, metrics, scores and flags."}
              </p>
            )}
          </Panel>
          <Panel variant="lite" title="What a run does">
            <ul className="space-y-3 text-[14px] text-ink-2">
              {Object.values(STAGE_TEXT).map((stage) => (
                <li key={stage.label}>
                  <span className="font-semibold text-ink">{stage.label}.</span> {stage.what}
                </li>
              ))}
              <li>Everything is one transaction: a failure leaves the previous results in place.</li>
            </ul>
          </Panel>
        </div>
      )}
    </>
  );
}
