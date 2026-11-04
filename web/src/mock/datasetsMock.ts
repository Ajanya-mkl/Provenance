// Mock dataset flow: replays datasets.json (generated from the real API against a
// temporary warehouse by web/scripts/generate_dataset_fixtures.py) and keeps
// uploads, mapping decisions and runs in memory for the session.
//
// Seeded datasets: a scored dataset with an excluded factor (no country column),
// a small gated one that maps but cannot be scored, student marks (not mappable,
// the agent's proposal) and a quarantined upload. An uploaded file becomes a new
// dataset that walks the whole flow; a file named like "dirty" is quarantined.
import { ApiError, mockVariant } from "../lib/api";
import type {
  ContractMapping,
  DatasetDetail,
  DatasetItem,
  DatasetResults,
  MappingRecord,
  MappingState,
  RunState,
  StageName,
  UploadReport,
} from "../lib/types";
import data from "./datasets.json";

type Fixtures = {
  ids: Record<"northwind" | "clean" | "marks" | "dirty", string>;
  uploads: Record<string, UploadReport>;
  gated_details: Record<string, DatasetDetail>;
  final_details: Record<string, DatasetDetail>;
  datasets: DatasetItem[];
  proposals: Record<string, MappingRecord & { decided_by: string; escalation_reasons: string[] }>;
  results: DatasetResults;
  routes: Record<string, unknown>;
};
const fixtures = data as unknown as Fixtures;
const SOURCE = fixtures.ids.northwind;
const STAGES: StageName[] = ["silver", "metrics", "scoring", "flags"];
const STAGE_MS = 1100;

function clone<T>(value: T): T {
  return JSON.parse(JSON.stringify(value)) as T;
}

/** The scored dataset's fixtures, re-addressed to another dataset id. */
function asDataset<T>(value: T, id: string): T {
  return JSON.parse(JSON.stringify(value).split(SOURCE).join(id)) as T;
}

interface DatasetState {
  item: DatasetItem;
  detail: DatasetDetail;
  mapping: MappingState;
  scored: boolean;
}

interface MockRun {
  run_id: string;
  dataset_id: string;
  started: number;
  fail?: string;
}

let state: Map<string, DatasetState> | null = null;
const runs = new Map<string, MockRun>();

function key(id: string): keyof Fixtures["ids"] | undefined {
  return (Object.keys(fixtures.ids) as (keyof Fixtures["ids"])[]).find((name) => fixtures.ids[name] === id);
}

function seed(): Map<string, DatasetState> {
  const map = new Map<string, DatasetState>();
  for (const item of fixtures.datasets) {
    const name = key(item.dataset_id)!;
    const proposal = fixtures.proposals[name];
    const mapping: MappingState =
      name === "northwind"
        ? clone(fixtures.routes[`/api/datasets/${SOURCE}/mapping`] as MappingState)
        : proposal
          ? { current: clone(proposal), history: [clone(proposal)] }
          : { current: null, history: [] };
    map.set(item.dataset_id, {
      item: clone(item),
      detail: clone(fixtures.final_details[name]),
      mapping,
      scored: item.display_status === "scored",
    });
  }
  return map;
}

function datasets(): Map<string, DatasetState> {
  state ??= seed();
  return state;
}

function find(id: string): DatasetState {
  const found = datasets().get(id);
  if (!found) throw new ApiError(404, "No such dataset.");
  return found;
}

function setStatus(entry: DatasetState, status: DatasetItem["status"], display = status as DatasetItem["display_status"]) {
  entry.item.status = status;
  entry.item.display_status = display;
  entry.detail.status = status;
  entry.detail.display_status = display;
}

/** For tests: forget uploads, decisions and runs. */
export function resetDatasetsMock() {
  state = null;
  runs.clear();
}

function newId(): string {
  return Array.from({ length: 12 }, () => Math.floor(Math.random() * 16).toString(16)).join("");
}

function wait(ms: number) {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

export async function mockUpload(
  file: File,
  name: string,
  onProgress?: (share: number) => void,
): Promise<UploadReport> {
  for (const share of [0.2, 0.45, 0.7, 0.9, 1]) {
    await wait(120);
    onProgress?.(share);
  }
  await wait(400);
  const lower = file.name.toLowerCase();
  if (lower.includes("dirty")) return clone(fixtures.uploads.dirty);
  if (lower.includes("marks")) {
    return {
      ...clone(fixtures.uploads.marks),
      status: "already_registered",
      message: `This file is already registered as 'Student marks' (dataset ${fixtures.ids.marks}, status gated); nothing changed.`,
    };
  }
  const id = newId();
  const label = name || file.name.replace(/\.csv$/i, "");
  const detail = asDataset(fixtures.gated_details.northwind, id);
  detail.name = label;
  detail.created_at = new Date().toISOString();
  const item: DatasetItem = {
    ...asDataset(fixtures.datasets.find((d) => d.dataset_id === SOURCE)!, id),
    name: label,
    status: "gated",
    display_status: "gated",
    created_at: detail.created_at,
  };
  datasets().set(id, { item, detail, mapping: { current: null, history: [] }, scored: false });
  return asDataset(fixtures.uploads.northwind, id);
}

function body<T>(init: RequestInit | undefined): T {
  return JSON.parse(String(init?.body ?? "{}")) as T;
}

function requireBy(by: string | undefined): string {
  const name = (by ?? "").trim();
  if (!name) throw new ApiError(400, "Reviewer name (by) is required.");
  return name;
}

function applySets(mapping: ContractMapping, sets: Record<string, string | null>): ContractMapping {
  const next = clone(mapping) as unknown as Record<string, unknown>;
  for (const [path, value] of Object.entries(sets)) {
    const [root, leaf] = path.split(".");
    if (leaf) {
      const target = (next[root] ?? {}) as Record<string, unknown>;
      target[leaf] = value;
      next[root] = target;
    } else {
      next[root] = value;
    }
    delete (next.evidence as Record<string, unknown>)[root];
  }
  const cancelled = next.cancelled as { col?: string | null } | null;
  if (cancelled && !cancelled.col) next.cancelled = null;
  next.confidence = null;
  return next as unknown as ContractMapping;
}

/** The validator's checks that a review screen can trip, with its wording. */
function validate(entry: DatasetState, mapping: ContractMapping): string[] {
  const columns = new Map(entry.detail.columns.map((column) => [column.column_name, column]));
  const errors: string[] = [];
  const used: [string, string][] = [];
  if (mapping.entity) used.push(["entity", mapping.entity]);
  if (mapping.event_date?.col) used.push(["event_date", mapping.event_date.col]);
  const outcome = mapping.outcome;
  if (outcome?.kind === "days_pair") {
    if (outcome.scheduled_col) used.push(["outcome.scheduled_col", outcome.scheduled_col]);
    if (outcome.actual_col) used.push(["outcome.actual_col", outcome.actual_col]);
  } else if (outcome?.col) {
    used.push(["outcome.col", outcome.col]);
  }
  if (mapping.country) used.push(["country", mapping.country]);
  if (mapping.cancelled?.col) used.push(["cancelled", mapping.cancelled.col]);
  const seen = new Map<string, string>();
  for (const [role, column] of used) {
    if (!columns.has(column)) errors.push(`${role}: column '${column}' does not exist in this dataset.`);
    if (seen.has(column)) errors.push(`Column '${column}' is used twice (${seen.get(column)} and ${role}).`);
    else seen.set(column, role);
  }
  const entity = mapping.entity ? columns.get(mapping.entity) : undefined;
  if (entity && !["string", "int"].includes(entity.inferred_type)) {
    errors.push(`entity: '${entity.column_name}' holds ${entity.inferred_type} values; an entity is a name or code.`);
  }
  for (const role of ["scheduled_col", "actual_col", "col"] as const) {
    const column = outcome?.[role] ? columns.get(outcome[role]!) : undefined;
    if (column && outcome?.kind !== "late_flag" && !["int", "float"].includes(column.inferred_type)) {
      errors.push(`outcome.${role}: '${column.column_name}' holds ${column.inferred_type} values, not numbers.`);
    }
  }
  return errors;
}

function runState(run: MockRun): RunState {
  const elapsed = Date.now() - run.started;
  const reached = Math.min(Math.floor(elapsed / STAGE_MS), STAGES.length);
  const done = reached >= STAGES.length;
  const entry = datasets().get(run.dataset_id);
  if (done && !run.fail && entry && !entry.scored) {
    entry.scored = true;
    setStatus(entry, "mapped", "scored");
  }
  const failedAt = run.fail ? Math.min(reached, 1) : -1;
  return {
    run_id: run.run_id,
    dataset_id: run.dataset_id,
    status: run.fail && reached >= 1 ? "failed" : done ? "succeeded" : reached === 0 && elapsed < 300 ? "queued" : "running",
    stage: run.fail && reached >= 1 ? "metrics" : done ? "flags" : STAGES[Math.max(0, Math.min(reached, STAGES.length - 1))],
    error: run.fail && reached >= 1 ? run.fail : null,
    started_at: new Date(run.started).toISOString(),
    finished_at: done || (run.fail && reached >= 1) ? new Date().toISOString() : null,
    stages: STAGES.map((name, index) => ({
      name,
      status:
        failedAt >= 0 && reached >= 1
          ? index < failedAt
            ? "done"
            : index === failedAt
              ? "failed"
              : "pending"
          : done || index < reached
            ? "done"
            : index === reached
              ? "running"
              : "pending",
    })),
  };
}

/** Dataset routes; undefined when the path is not a dataset route. */
export function mockDatasetFetch(path: string, init?: RequestInit): unknown {
  const method = init?.method ?? "GET";
  const [base, query] = path.split("?");
  const params = new URLSearchParams(query ?? "");
  const dataset = params.get("dataset");

  if (base === "/api/datasets" && method === "GET") {
    return { items: [...datasets().values()].map((entry) => clone(entry.item)) };
  }
  if (base.startsWith("/api/runs/")) {
    const run = runs.get(decodeURIComponent(base.slice("/api/runs/".length)));
    if (!run) throw new ApiError(404, "No such run.");
    return runState(run);
  }
  const match = base.match(/^\/api\/datasets\/([0-9a-f]{12})(?:\/(.*))?$/);
  if (match) {
    const [, id, rest = ""] = match;
    const entry = find(id);
    if (rest === "" && method === "GET") return clone(entry.detail);
    if (rest === "mapping" && method === "GET") return clone(entry.mapping);
    if (rest === "results") {
      if (!entry.scored) throw new ApiError(404, "This dataset has not been scored yet.");
      const results = asDataset(fixtures.results, id);
      return results;
    }
    if (rest === "mapping/propose") {
      requireBy(body<{ by?: string }>(init).by);
      if (entry.detail.status === "quarantined") {
        throw new ApiError(400, `Dataset ${id} is quarantined; only a gated dataset can be mapped.`);
      }
      const proposal = asDataset(fixtures.proposals.northwind, id);
      proposal.version = entry.mapping.history.length + 1;
      entry.mapping = { current: clone(proposal), history: [...entry.mapping.history, clone(proposal)] };
      return proposal;
    }
    if (rest === "run") {
      if (mockVariant() === "busy") {
        throw new ApiError(
          409,
          "Another writer is running (a pipeline run is writing to the warehouse (provenance_full_pipeline)). " +
            "Only one run can write to the warehouse at a time; try again when it finishes.",
          "busy",
        );
      }
      if (entry.detail.status !== "mapped") {
        throw new ApiError(400, `Dataset ${id} is ${entry.detail.status}; only a dataset with an approved mapping ('mapped') can be scored.`);
      }
      const approved = entry.mapping.history.find((record) => record.status === "approved");
      if (approved && !approved.scoreable) {
        throw new ApiError(400, `The approved mapping is not scoreable: ${approved.reasons.scoreable}.`);
      }
      const run: MockRun = {
        run_id: crypto.randomUUID(),
        dataset_id: id,
        started: Date.now(),
        // ?mock=runfail: the run stops at metrics with the error the real scorer gives.
        fail:
          mockVariant() === "runfail"
            ? "Not scoreable: only 2 eligible quarter(s) after the flagship's rules (at least 3 needed; " +
              "a quarter is eligible when it is complete and an entity has 50 or more rows in it)."
            : undefined,
      };
      runs.set(run.run_id, run);
      return { run_id: run.run_id, dataset_id: id, status: "queued" };
    }
    const decision = rest.match(/^mapping\/([^/]+)\/(approve|reject|edit)$/);
    if (decision) {
      const [, mappingId, verb] = decision;
      const record = entry.mapping.history.find((item) => item.mapping_id === mappingId);
      if (!record) throw new ApiError(404, `No mapping '${mappingId}'.`);
      const request = body<{ by?: string; reason?: string | null; set?: Record<string, string | null> }>(init);
      const by = requireBy(request.by);
      if (record.status !== "proposed") throw new ApiError(409, `Mapping ${mappingId} is ${record.status}, not proposed.`);
      if (verb === "edit") {
        const edited = applySets(record.proposal, request.set ?? {});
        const errors = validate(entry, edited);
        if (errors.length) throw new ApiError(400, errors.join("; "));
        edited.notes = `edited by ${by}: ${Object.entries(request.set ?? {}).map(([k, v]) => `${k}=${v}`).join(", ")}`;
        const next: MappingRecord = {
          ...clone(record),
          mapping_id: crypto.randomUUID(),
          version: entry.mapping.history.length + 1,
          proposal: edited,
          source: "human",
          status: "proposed",
          mappable: Boolean(edited.entity && edited.event_date?.col && edited.outcome?.kind),
          validation: { ok: true, errors: [], decided_by: "human" },
          created_by: by,
          created_at: new Date().toISOString(),
          prompt_tokens: 0,
          completion_tokens: 0,
        };
        record.status = "superseded";
        record.superseded_by = next.mapping_id;
        entry.mapping = { current: next, history: [...entry.mapping.history, next] };
        return clone(next);
      }
      if (verb === "approve") {
        if (!record.mappable) {
          throw new ApiError(400, `Mapping ${mappingId} is not mappable (${record.reasons.mappable}); edit it first.`);
        }
        Object.assign(record, { status: "approved", decided_by: by, reason: request.reason ?? null, decided_at: new Date().toISOString() });
        setStatus(entry, "mapped", entry.scored ? "scored" : "mapped");
        entry.mapping.current = record;
        return { mapping_id: mappingId, status: "approved", dataset_status: "mapped" };
      }
      if (!(request.reason ?? "").trim()) throw new ApiError(400, "A reason is required to reject a mapping.");
      Object.assign(record, { status: "rejected", decided_by: by, reason: request.reason, decided_at: new Date().toISOString() });
      return { mapping_id: mappingId, status: "rejected" };
    }
    throw new ApiError(404, "Not in the mock fixtures.");
  }

  // Flags and suppliers of a dataset (?dataset=, or a 'ds_<id>:' event id).
  const eventDataset = decodeURIComponent(base).match(/^\/api\/flags\/ds_([0-9a-f]{12}):/)?.[1];
  const target = dataset && dataset !== "dataco" ? dataset : eventDataset;
  if (target && (base.startsWith("/api/flags") || base.startsWith("/api/suppliers/"))) {
    const entry = find(target);
    if (base === "/api/flags") {
      const items = entry.scored ? asDataset(fixtures.results, target).flags : [];
      const filtered = items.filter(
        (flag) =>
          (!params.get("tier") || flag.new_tier === params.get("tier")) &&
          (!params.get("confidence") || flag.confidence === params.get("confidence")),
      );
      return { items: filtered, count: filtered.length };
    }
    const sourcePath = path.split(target).join(SOURCE);
    const route = fixtures.routes[sourcePath] ?? fixtures.routes[decodeURIComponent(sourcePath)];
    if (!route) throw new ApiError(404, "Not in the mock fixtures.");
    return asDataset(route, target);
  }
  return undefined;
}
