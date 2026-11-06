import type {
  AgentSummary,
  Approvals,
  Audit,
  DatasetDetail,
  DatasetHistory,
  DatasetItem,
  DatasetResults,
  Evaluation,
  FlagDetail,
  FlagList,
  Health,
  Investigation,
  InvestigationRunState,
  MappingRecord,
  MappingState,
  MarketMix,
  QualityRow,
  RunState,
  ScoreBreakdown,
  SupplierHistory,
  UploadReport,
} from "./types";

export class ApiError extends Error {
  readonly status: number;
  readonly code: string | undefined;

  constructor(status: number, message: string, code?: string) {
    super(message);
    this.status = status;
    this.code = code;
  }
}

const MOCK_KEY = "provenance.mock";

/** Mock mode: `npm run dev:mock`, or `?mock=1` once (remembered for the tab). */
export function isMock(): boolean {
  if (import.meta.env.MODE === "mock") return true;
  try {
    const flag = new URLSearchParams(window.location.search).get("mock");
    if (flag !== null) {
      if (flag === "0") sessionStorage.removeItem(MOCK_KEY);
      else sessionStorage.setItem(MOCK_KEY, flag);
    }
    return sessionStorage.getItem(MOCK_KEY) !== null && flag !== "0";
  } catch {
    return false;
  }
}

/**
 * `?mock=locked` makes approve/reject answer 409; `?mock=busy` also makes dataset and
 * agent runs answer 409 'busy'; `?mock=nokey` makes an agent run answer 400 (no key).
 */
export function mockVariant(): string | null {
  try {
    return sessionStorage.getItem(MOCK_KEY);
  } catch {
    return null;
  }
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  if (isMock()) {
    const { mockFetch } = await import("../mock/mockApi");
    return mockFetch<T>(path, init);
  }
  let response: Response;
  try {
    response = await fetch(path, {
      ...init,
      headers: { "Content-Type": "application/json", ...init?.headers },
    });
  } catch {
    throw new ApiError(0, "The API is not reachable. Start it with uvicorn on port 8000.");
  }
  const body = await response.json().catch(() => ({}));
  if (!response.ok) {
    const detail = typeof body.detail === "string" ? body.detail : response.statusText;
    throw new ApiError(response.status, detail, body.code);
  }
  return body as T;
}

const enc = encodeURIComponent;

/** The DataCo flagship, or an onboarded dataset's id. */
export const DATACO = "dataco";
const datasetQuery = (dataset?: string, joiner = "?") =>
  dataset && dataset !== DATACO ? `${joiner}dataset=${enc(dataset)}` : "";

function post<T>(path: string, body: unknown): Promise<T> {
  return request<T>(path, { method: "POST", body: JSON.stringify(body) });
}

/** Multipart upload with progress (fetch cannot report upload progress). */
function uploadFile(file: File, name: string, onProgress?: (share: number) => void): Promise<UploadReport> {
  if (isMock()) {
    return import("../mock/datasetsMock").then(({ mockUpload }) => mockUpload(file, name, onProgress));
  }
  return new Promise((resolve, reject) => {
    const form = new FormData();
    form.append("file", file);
    if (name) form.append("name", name);
    const xhr = new XMLHttpRequest();
    xhr.open("POST", "/api/datasets/upload");
    xhr.responseType = "json";
    xhr.upload.onprogress = (event) => {
      if (event.lengthComputable) onProgress?.(event.loaded / event.total);
    };
    xhr.onerror = () =>
      reject(new ApiError(0, "The API is not reachable. Start it with python -m provenance.api."));
    xhr.onload = () => {
      const body = (xhr.response ?? {}) as { detail?: string; code?: string };
      if (xhr.status >= 200 && xhr.status < 300) resolve(body as unknown as UploadReport);
      else reject(new ApiError(xhr.status, typeof body.detail === "string" ? body.detail : xhr.statusText, body.code));
    };
    xhr.send(form);
  });
}

export const api = {
  health: () => request<Health>("/api/health"),
  flags: (
    params: { tier?: string; quarter?: string; confidence?: string; dataset?: string } = {},
  ) => {
    const { dataset, ...filters } = params;
    const query = new URLSearchParams(
      Object.entries({ ...filters, dataset: dataset === DATACO ? undefined : dataset }).filter(
        (entry): entry is [string, string] => Boolean(entry[1]),
      ),
    ).toString();
    return request<FlagList>(`/api/flags${query ? `?${query}` : ""}`);
  },
  flag: (eventId: string) => request<FlagDetail>(`/api/flags/${enc(eventId)}`),
  history: (key: string) => request<SupplierHistory>(`/api/suppliers/${enc(key)}/history`),
  datasetHistory: (key: string, dataset: string) =>
    request<DatasetHistory>(`/api/suppliers/${enc(key)}/history${datasetQuery(dataset)}`),
  score: (key: string, quarter: string, dataset?: string) =>
    request<ScoreBreakdown>(
      `/api/suppliers/${enc(key)}/score/${enc(quarter)}${datasetQuery(dataset)}`,
    ),
  markets: (key: string, quarter: string) =>
    request<MarketMix>(`/api/suppliers/${enc(key)}/markets/${enc(quarter)}`),
  investigation: (eventId: string) =>
    request<Investigation>(`/api/investigations/${enc(eventId)}`),
  startInvestigation: (eventId: string) =>
    post<{ run_id: string; event_id: string; status: string }>(
      `/api/investigations/${enc(eventId)}/run`,
      {},
    ),
  investigationRun: (runId: string) => request<InvestigationRunState>(`/api/runs/${enc(runId)}`),
  agentSummary: () => request<AgentSummary>("/api/agent/summary"),
  approvals: () => request<Approvals>("/api/approvals"),
  approve: (actionId: string, by: string, reason: string) =>
    request<{ status: string; action_id: string; effect?: string | null }>(
      `/api/approvals/${enc(actionId)}/approve`,
      { method: "POST", body: JSON.stringify({ by, reason: reason || null }) },
    ),
  reject: (actionId: string, by: string, reason: string) =>
    request<{ status: string; action_id: string }>(`/api/approvals/${enc(actionId)}/reject`, {
      method: "POST",
      body: JSON.stringify({ by, reason }),
    }),
  audit: () => request<Audit>("/api/audit?limit=50"),
  quality: () => request<{ items: QualityRow[] }>("/api/quality"),
  evaluation: () => request<Evaluation>("/api/evaluation"),

  // Onboarded datasets (Phase 10c-2).
  datasets: () => request<{ items: DatasetItem[] }>("/api/datasets"),
  dataset: (id: string) => request<DatasetDetail>(`/api/datasets/${enc(id)}`),
  upload: uploadFile,
  mapping: (id: string) => request<MappingState>(`/api/datasets/${enc(id)}/mapping`),
  propose: (id: string, by: string) =>
    post<MappingRecord & { decided_by: string; escalation_reasons: string[] }>(
      `/api/datasets/${enc(id)}/mapping/propose`,
      { by },
    ),
  editMapping: (id: string, mappingId: string, by: string, set: Record<string, string | null>) =>
    post<MappingRecord>(`/api/datasets/${enc(id)}/mapping/${enc(mappingId)}/edit`, { by, set }),
  approveMapping: (id: string, mappingId: string, by: string, reason: string) =>
    post<{ mapping_id: string; status: string; dataset_status: string }>(
      `/api/datasets/${enc(id)}/mapping/${enc(mappingId)}/approve`,
      { by, reason: reason || null },
    ),
  rejectMapping: (id: string, mappingId: string, by: string, reason: string) =>
    post<{ mapping_id: string; status: string }>(
      `/api/datasets/${enc(id)}/mapping/${enc(mappingId)}/reject`,
      { by, reason },
    ),
  startRun: (id: string) =>
    post<{ run_id: string; dataset_id: string; status: string }>(`/api/datasets/${enc(id)}/run`, {}),
  run: (runId: string) => request<RunState>(`/api/runs/${enc(runId)}`),
  results: (id: string) => request<DatasetResults>(`/api/datasets/${enc(id)}/results`),
};
