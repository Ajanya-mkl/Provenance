import { ArrowRight, ChevronRight, Database, FileUp, ShieldAlert, Upload, X } from "lucide-react";
import { useId, useRef, useState } from "react";
import type { DragEvent } from "react";
import { Link, useNavigate } from "react-router";

import { FieldLabel, ResultMark, SeverityBadge, StatusChip } from "../components/datasets";
import { EmptyState, ErrorState, LoadingBlock, PageHeader, Panel } from "../components/ui";
import { api, ApiError } from "../lib/api";
import { bytes, inputClass } from "../lib/datasetUi";
import { date } from "../lib/format";
import type { DatasetItem, GateRuleResult, UploadReport } from "../lib/types";
import { useApi } from "../lib/useApi";

const MAX_UPLOAD_MB = 50;
const FLOW_STEPS = [
  { title: "Upload", text: "A CSV is profiled and checked by rules generated from its own columns." },
  { title: "Map", text: "Match its columns to the shipment contract, then a named reviewer approves." },
  { title: "Run", text: "Build silver, metrics, scores and flags in the background." },
  { title: "Results", text: "Read scores, flags and what was left out, then let the agent investigate." },
];

function nextStep(item: DatasetItem): { to: string; label: string } {
  const base = `/datasets/${item.dataset_id}`;
  switch (item.display_status) {
    case "gated":
      return { to: `${base}/mapping`, label: "Review mapping" };
    case "mapped":
      return { to: `${base}/run`, label: "Run scoring" };
    case "scored":
      return { to: `${base}/results`, label: "See results" };
    default:
      return { to: base, label: "See why" };
  }
}

export function DatasetsPage() {
  const datasets = useApi("datasets", api.datasets);
  const items = datasets.data?.items ?? [];
  return (
    <>
      <PageHeader
        title="Datasets"
        meta="Your own shipment files, onboarded next to DataCo: profiled, gated, mapped and scored."
        actions={
          <Link to="/datasets/upload" className="glass-pill pill-accent px-4 py-2 text-sm">
            <Upload aria-hidden size={16} /> Upload dataset
          </Link>
        }
      />
      <ol aria-label="How it works" className="mb-6 grid gap-4 sm:grid-cols-2 xl:grid-cols-4">
        {FLOW_STEPS.map((step, index) => (
          <li key={step.title} className="glass-lite p-4">
            <p className="text-[15px] font-semibold">
              <span aria-hidden className="tabular mr-2 text-ink-3">
                {index + 1}
              </span>
              {step.title}
            </p>
            <p className="mt-1 text-[13px] text-ink-2">{step.text}</p>
          </li>
        ))}
      </ol>
      <section aria-label="Dataset list" className="glass p-2">
        {datasets.error ? (
          <ErrorState error={datasets.error} onRetry={datasets.reload} />
        ) : datasets.loading ? (
          <div className="p-4">
            <LoadingBlock rows={5} label="Loading datasets" />
          </div>
        ) : items.length === 0 ? (
          <EmptyState icon={<Database size={22} />} title="No datasets yet">
            Upload a shipment CSV to profile it, map it onto the contract and score it.
          </EmptyState>
        ) : (
          <table className="tabular w-full border-collapse text-left text-[14px]">
            <caption className="sr-only">Onboarded datasets</caption>
            <thead>
              <tr className="text-[13px] text-ink-3">
                <th scope="col" className="px-4 py-3 font-medium">Dataset</th>
                <th scope="col" className="px-4 py-3 font-medium">Status</th>
                <th scope="col" className="px-4 py-3 text-right font-medium max-sm:hidden">Rows</th>
                <th scope="col" className="px-4 py-3 text-right font-medium max-md:hidden">Columns</th>
                <th scope="col" className="px-4 py-3 font-medium max-md:hidden">Uploaded</th>
                <th scope="col" className="px-4 py-3"><span className="sr-only">Next step</span></th>
              </tr>
            </thead>
            <tbody>
              {items.map((item) => {
                const next = nextStep(item);
                return (
                  <tr key={item.dataset_id} className="border-t border-line">
                    <td className="px-4 py-3.5">
                      <Link to={`/datasets/${item.dataset_id}`} className="font-semibold text-ink hover:underline">
                        {item.name}
                      </Link>
                      <span className="block text-[12px] text-ink-3">{item.dataset_id}</span>
                    </td>
                    <td className="px-4 py-3.5"><StatusChip status={item.display_status} /></td>
                    <td className="px-4 py-3.5 text-right max-sm:hidden">{item.row_count?.toLocaleString() ?? "–"}</td>
                    <td className="px-4 py-3.5 text-right max-md:hidden">{item.column_count ?? "–"}</td>
                    <td className="px-4 py-3.5 text-ink-2 max-md:hidden">{date(item.created_at)}</td>
                    <td className="px-4 py-3.5 text-right">
                      <Link to={next.to} className="inline-flex items-center gap-1 text-[13px] font-medium text-ink-2 hover:text-ink">
                        {next.label} <ChevronRight aria-hidden size={15} />
                      </Link>
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        )}
      </section>
    </>
  );
}

/** The quality gate's rules in plain language: what, why, result, hard or tolerance. */
export function GateRules({ rules, onlyFailures = false }: { rules: GateRuleResult[]; onlyFailures?: boolean }) {
  const shown = onlyFailures ? rules.filter((rule) => !rule.success) : rules;
  return (
    <ul className="divide-y divide-line">
      {shown.map((rule) => (
        <li key={rule.rule_id} className="flex flex-wrap items-start gap-x-4 gap-y-1 py-3">
          <div className="min-w-0 flex-1">
            <p className="text-[14px] font-medium text-ink">
              {rule.column ? <span className="text-ink-2">{rule.column}: </span> : null}
              {rule.check}
            </p>
            <p className="text-[13px] text-ink-3">Why: {rule.why}</p>
            {!rule.success && <p className="text-[13px] text-ink-2">{rule.detail}</p>}
          </div>
          <SeverityBadge severity={rule.severity} />
          <ResultMark ok={rule.success} />
        </li>
      ))}
    </ul>
  );
}

function UploadOutcome({ report }: { report: UploadReport }) {
  const navigate = useNavigate();
  const id = report.dataset_id;
  if (report.status === "rejected" || report.status === "failed") {
    return (
      <Panel title="The file was not onboarded" description="Nothing was stored.">
        <ul className="list-disc space-y-1 pl-5 text-[14px] text-ink-2">
          {(report.problems.length ? report.problems : [report.message]).map((problem) => (
            <li key={problem}>{problem}</li>
          ))}
        </ul>
      </Panel>
    );
  }
  if (report.status === "already_registered") {
    return (
      <Panel title="Already registered" description={report.message}>
        <Link to={`/datasets/${id}`} className="glass-pill px-4 py-2 text-sm">
          Open the existing dataset <ArrowRight aria-hidden size={15} />
        </Link>
      </Panel>
    );
  }
  const gate = report.gate;
  const quarantined = report.status === "quarantined";
  return (
    <Panel
      title={quarantined ? "Quarantined by the quality gate" : "Passed the quality gate"}
      description={
        quarantined
          ? "A hard rule failed, so the file was not loaded into bronze."
          : `${report.rows_loaded.toLocaleString()} rows loaded. Next: review the column mapping.`
      }
      actions={
        quarantined ? (
          <Link to={`/datasets/${id}`} className="glass-pill px-4 py-2 text-sm">Profile</Link>
        ) : (
          <button type="button" onClick={() => navigate(`/datasets/${id}/mapping`)} className="glass-pill pill-accent px-4 py-2 text-sm">
            Review mapping <ArrowRight aria-hidden size={15} />
          </button>
        )
      }
    >
      {quarantined && (
        <div role="status" className="mb-5 flex gap-3 rounded-[14px] bg-[color-mix(in_srgb,var(--danger)_12%,transparent)] p-4 text-[14px] text-ink">
          <ShieldAlert aria-hidden size={18} className="mt-0.5 shrink-0 text-danger" />
          <p>
            The batch was moved to <code className="font-mono text-[13px]">{report.quarantine_path}</code> with
            its gate report. It is kept for inspection and never reaches silver. Fix the rows listed below and
            upload the corrected file.
          </p>
        </div>
      )}
      {report.profile && (
        <p className="mb-4 text-[14px] text-ink-2">
          Pre-checks passed: {report.profile.row_count.toLocaleString()} rows × {report.profile.column_count}{" "}
          columns, {report.profile.encoding}, delimiter “{report.profile.delimiter}”.
          {gate && ` ${gate.rules.length} generated rules: ${gate.hard_failures} hard failure(s), ${gate.warnings} warning(s).`}
        </p>
      )}
      {gate && (
        <>
          {gate.rules.some((rule) => !rule.success) && (
            <>
              <h3 className="mb-1 text-[15px] font-semibold">Rules that failed</h3>
              <GateRules rules={gate.rules} onlyFailures />
            </>
          )}
          <details className="mt-4 text-sm">
            <summary className="cursor-pointer text-ink-2 hover:text-ink">All {gate.rules.length} rules</summary>
            <GateRules rules={gate.rules} />
          </details>
        </>
      )}
    </Panel>
  );
}

export function UploadPage() {
  const inputId = useId();
  const nameId = useId();
  const input = useRef<HTMLInputElement>(null);
  const [file, setFile] = useState<File | null>(null);
  const [name, setName] = useState("");
  const [problem, setProblem] = useState<string | null>(null);
  const [dragging, setDragging] = useState(false);
  const [progress, setProgress] = useState<number | null>(null);
  const [report, setReport] = useState<UploadReport | null>(null);
  const [error, setError] = useState<ApiError | null>(null);

  const choose = (candidate: File | undefined) => {
    setReport(null);
    setError(null);
    if (!candidate) return;
    if (!candidate.name.toLowerCase().endsWith(".csv")) {
      setProblem(`${candidate.name} is not a .csv file. Only CSV files can be uploaded.`);
      setFile(null);
      return;
    }
    if (candidate.size > MAX_UPLOAD_MB * 1024 * 1024) {
      setProblem(`${candidate.name} is ${bytes(candidate.size)}; the limit is ${MAX_UPLOAD_MB} MB.`);
      setFile(null);
      return;
    }
    if (candidate.size === 0) {
      setProblem(`${candidate.name} is empty.`);
      setFile(null);
      return;
    }
    setProblem(null);
    setFile(candidate);
    setName((current) => current || candidate.name.replace(/\.csv$/i, ""));
  };

  const onDrop = (event: DragEvent<HTMLElement>) => {
    event.preventDefault();
    setDragging(false);
    choose(event.dataTransfer.files[0]);
  };

  const submit = async () => {
    if (!file) return;
    setProgress(0);
    setError(null);
    try {
      setReport(await api.upload(file, name.trim(), setProgress));
    } catch (caught) {
      setError(caught instanceof ApiError ? caught : new ApiError(0, String(caught)));
    } finally {
      setProgress(null);
    }
  };

  const uploading = progress !== null;
  return (
    <>
      <PageHeader title="Upload a CSV" meta={`Shipment data with a supplier or category, a date and lateness. Up to ${MAX_UPLOAD_MB} MB.`} />
      <div className="grid gap-6 xl:grid-cols-[minmax(0,1fr)_minmax(0,1.3fr)]">
        <Panel title="File" description="Pre-checks, profile, generated quality rules, gate, bronze.">
          <div
            onDragOver={(event) => {
              event.preventDefault();
              setDragging(true);
            }}
            onDragLeave={() => setDragging(false)}
            onDrop={onDrop}
            className={`flex flex-col items-center rounded-[18px] border-2 border-dashed px-6 py-10 text-center transition-colors ${
              dragging ? "border-[var(--accent)] bg-[color-mix(in_srgb,var(--accent)_10%,transparent)]" : "border-line"
            }`}
          >
            <FileUp aria-hidden size={28} className="mb-3 text-ink-3" />
            <p className="text-[15px] font-semibold">Drop a CSV here</p>
            <p className="mt-1 text-[13px] text-ink-3">or</p>
            <label htmlFor={inputId} className="glass-pill mt-3 cursor-pointer px-4 py-2 text-sm">
              Choose a file
            </label>
            <input
              id={inputId}
              ref={input}
              type="file"
              accept=".csv,text/csv"
              className="sr-only"
              onChange={(event) => choose(event.target.files?.[0])}
            />
          </div>
          {problem && (
            <p role="alert" className="mt-4 text-[14px] text-danger">
              {problem}
            </p>
          )}
          {file && (
            <div className="mt-5 space-y-4">
              <div className="flex items-center justify-between gap-3 rounded-[14px] bg-[color-mix(in_srgb,var(--ink)_6%,transparent)] px-4 py-3">
                <p className="min-w-0 truncate text-[14px]">
                  <span className="font-semibold">{file.name}</span>{" "}
                  <span className="text-ink-3">{bytes(file.size)}</span>
                </p>
                <button
                  type="button"
                  aria-label="Remove file"
                  onClick={() => {
                    setFile(null);
                    if (input.current) input.current.value = "";
                  }}
                  className="text-ink-3 hover:text-ink"
                  disabled={uploading}
                >
                  <X aria-hidden size={16} />
                </button>
              </div>
              <div>
                <FieldLabel htmlFor={nameId}>Dataset name</FieldLabel>
                <input id={nameId} value={name} onChange={(event) => setName(event.target.value)} className={inputClass} maxLength={100} />
              </div>
              {uploading && (
                <div>
                  <div
                    role="progressbar"
                    aria-label="Upload progress"
                    aria-valuemin={0}
                    aria-valuemax={100}
                    aria-valuenow={Math.round(progress * 100)}
                    className="h-2 overflow-hidden rounded-full bg-[color-mix(in_srgb,var(--ink)_10%,transparent)]"
                  >
                    <div className="h-full rounded-full bg-accent transition-[width] duration-200" style={{ width: `${Math.round(progress * 100)}%` }} />
                  </div>
                  <p className="mt-2 text-[13px] text-ink-3">
                    {progress < 1 ? `Uploading ${Math.round(progress * 100)}%` : "Profiling and running the quality gate…"}
                  </p>
                </div>
              )}
              <button type="button" onClick={submit} disabled={uploading} className="glass-pill pill-accent px-4 py-2 text-sm">
                <Upload aria-hidden size={16} /> {uploading ? "Onboarding…" : "Upload and check"}
              </button>
            </div>
          )}
        </Panel>
        <div aria-live="polite">
          {error ? (
            <Panel title="Upload refused">
              <ErrorState error={error} />
            </Panel>
          ) : report ? (
            <UploadOutcome report={report} />
          ) : (
            <Panel variant="lite" title="What happens next">
              <ol className="list-decimal space-y-2 pl-5 text-[14px] text-ink-2">
                <li>Pre-checks: CSV only, size and column limits, readable encoding.</li>
                <li>Profile: each column's type, empties, distinct values and ranges.</li>
                <li>Generated quality rules: hard rules quarantine, tolerance rules warn.</li>
                <li>Gate: a passing file is loaded into its own bronze schema.</li>
                <li>Then map its columns onto the contract, run scoring and read the results.</li>
              </ol>
            </Panel>
          )}
        </div>
      </div>
    </>
  );
}
