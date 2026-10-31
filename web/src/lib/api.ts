import type {
  Approvals,
  Audit,
  Evaluation,
  FlagDetail,
  FlagList,
  Health,
  Investigation,
  MarketMix,
  QualityRow,
  ScoreBreakdown,
  SupplierHistory,
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

/** `?mock=locked` makes approve/reject answer 409, to demo the locked state. */
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

export const api = {
  health: () => request<Health>("/api/health"),
  flags: (params: { tier?: string; quarter?: string; confidence?: string } = {}) => {
    const query = new URLSearchParams(
      Object.entries(params).filter((entry): entry is [string, string] => Boolean(entry[1])),
    ).toString();
    return request<FlagList>(`/api/flags${query ? `?${query}` : ""}`);
  },
  flag: (eventId: string) => request<FlagDetail>(`/api/flags/${enc(eventId)}`),
  history: (key: string) => request<SupplierHistory>(`/api/suppliers/${enc(key)}/history`),
  score: (key: string, quarter: string) =>
    request<ScoreBreakdown>(`/api/suppliers/${enc(key)}/score/${enc(quarter)}`),
  markets: (key: string, quarter: string) =>
    request<MarketMix>(`/api/suppliers/${enc(key)}/markets/${enc(quarter)}`),
  investigation: (eventId: string) =>
    request<Investigation>(`/api/investigations/${enc(eventId)}`),
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
};
