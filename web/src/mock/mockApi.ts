// Mock API: serves fixtures.json (generated from the real API against a copy of
// the warehouse by web/scripts/generate_mock_fixtures.py) and keeps approve /
// reject decisions in memory for the session.
import { ApiError, mockVariant } from "../lib/api";
import type { Approvals, AuditEntry } from "../lib/types";
import { mockDatasetFetch } from "./datasetsMock";
import fixtures from "./fixtures.json";

type Route = { status: number; body: unknown };
const routes = fixtures as unknown as Record<string, Route>;

const LATENCY_MS = 260;

function clone<T>(value: T): T {
  return JSON.parse(JSON.stringify(value)) as T;
}

let approvals: Approvals | null = null;
let nextSeq = 1000;

function approvalsState(): Approvals {
  approvals ??= clone(routes["/api/approvals"].body as Approvals);
  return approvals;
}

function wait(ms: number) {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

function decide(path: string, init: RequestInit): unknown {
  const match = path.match(/^\/api\/approvals\/([^/]+)\/(approve|reject)$/);
  if (!match) throw new ApiError(404, "Not found");
  if (mockVariant() === "locked") {
    throw new ApiError(
      409,
      "The warehouse is locked by another writer (for example a Dagster run). " +
        "Nothing was changed; try again when it finishes.",
      "locked",
    );
  }
  const [, rawId, verb] = match;
  const id = decodeURIComponent(rawId);
  const body = JSON.parse(String(init.body ?? "{}")) as { by?: string; reason?: string | null };
  const by = (body.by ?? "").trim();
  if (!by) throw new ApiError(400, "Reviewer name (by) is required.");
  if (verb === "reject" && !(body.reason ?? "").trim()) {
    throw new ApiError(400, "A reason is required to reject.");
  }
  const state = approvalsState();
  const item = state.items.find((candidate) => candidate.action_id === id);
  if (!item) throw new ApiError(404, `No action matches '${id}'.`);
  if (item.status !== "proposed") {
    throw new ApiError(409, `Action ${id} is already ${item.status}.`);
  }
  const ts = new Date().toISOString();
  const entry = (event: string, detail: Record<string, unknown>): AuditEntry => ({
    seq: nextSeq++,
    ts,
    actor: by,
    event,
    detail: { action_id: id, ...detail },
  });
  if (verb === "approve") {
    item.status = "applied";
    item.decided_by = by;
    const effect =
      item.action_type === "watchlist"
        ? "agent.watchlist"
        : item.action_type === "request_review"
          ? "agent.review_requests"
          : null;
    item.audit.push(entry("action_approved", { reason: body.reason ?? null }));
    item.audit.push(entry("action_applied", { action_type: item.action_type, effect }));
    state.pending -= 1;
    return { status: "applied", applied: true, action_id: id, effect };
  }
  item.status = "rejected";
  item.decided_by = by;
  item.audit.push(entry("action_rejected", { reason: body.reason }));
  state.pending -= 1;
  return { status: "rejected", action_id: id };
}

const servedOnce = new Set<string>();

export async function mockFetch<T>(path: string, init?: RequestInit): Promise<T> {
  await wait(LATENCY_MS);
  if (mockVariant() === "busy" && init?.method !== "POST") {
    if (servedOnce.has(path)) {
      throw new ApiError(
        409,
        "The warehouse is locked by another writer (for example a Dagster run).",
        "locked",
      );
    }
    servedOnce.add(path);
  }
  const dataset = mockDatasetFetch(path, init);
  if (dataset !== undefined) return clone(dataset) as T;
  if (init?.method === "POST") return decide(path, init) as T;
  if (path === "/api/approvals") return clone(approvalsState()) as T;

  const [base, query] = path.split("?");
  let route = routes[path] ?? routes[base];
  if (base === "/api/flags" && query) {
    const params = new URLSearchParams(query);
    const all = (routes["/api/flags"].body as { items: Record<string, unknown>[] }).items;
    const items = all.filter(
      (flag) =>
        (!params.get("tier") || flag.new_tier === params.get("tier")) &&
        (!params.get("quarter") || flag.quarter === params.get("quarter")) &&
        (!params.get("confidence") || flag.confidence === params.get("confidence")),
    );
    route = { status: 200, body: { items, count: items.length } };
  }
  if (!route) throw new ApiError(404, "Not in the mock fixtures.");
  if (route.status >= 400) {
    const detail = (route.body as { detail?: string }).detail ?? "Not found";
    throw new ApiError(route.status, detail);
  }
  return clone(route.body) as T;
}
