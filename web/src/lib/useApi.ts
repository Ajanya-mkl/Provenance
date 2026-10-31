import { useCallback, useEffect, useState, useSyncExternalStore } from "react";

import { ApiError } from "./api";

export interface Resource<T> {
  data: T | undefined;
  error: ApiError | undefined;
  loading: boolean;
  /** True when `data` is the last good copy because the warehouse is locked. */
  stale: boolean;
  reload: () => void;
}

// Last successful response per key. When a pipeline run holds the warehouse the
// API answers 409; screens keep showing this copy instead of an error panel.
const lastGood = new Map<string, unknown>();
const RETRY_WHILE_BUSY_MS = 10_000;

// Keys currently being served from lastGood; the shell shows one banner for all.
const staleKeys = new Set<string>();
const listeners = new Set<() => void>();
let staleSnapshot = false;

function setStale(key: string, stale: boolean) {
  const changed = stale ? !staleKeys.has(key) : staleKeys.has(key);
  if (!changed) return;
  if (stale) staleKeys.add(key);
  else staleKeys.delete(key);
  staleSnapshot = staleKeys.size > 0;
  listeners.forEach((listener) => listener());
}

/** Whether any screen is showing last-loaded data because the pipeline is running. */
export function usePipelineBusy(): boolean {
  return useSyncExternalStore(
    (listener) => {
      listeners.add(listener);
      return () => listeners.delete(listener);
    },
    () => staleSnapshot,
    () => false,
  );
}

export function isLocked(error: unknown): boolean {
  return error instanceof ApiError && error.status === 409 && error.code === "locked";
}

/** For tests: forget cached responses and banner state. */
export function resetApiCache() {
  lastGood.clear();
  staleKeys.clear();
  staleSnapshot = false;
  listeners.forEach((listener) => listener());
}

/** Load once per key; `key` null skips loading (for dependent requests). */
export function useApi<T>(key: string | null, load: () => Promise<T>): Resource<T> {
  const [state, setState] = useState<{
    key: string | null;
    data?: T;
    error?: ApiError;
    stale?: boolean;
  }>({ key: null });
  const [nonce, setNonce] = useState(0);

  useEffect(() => {
    if (key === null) return;
    let active = true;
    load().then(
      (data) => {
        lastGood.set(key, data);
        setStale(key, false);
        if (active) setState({ key, data });
      },
      (error: unknown) => {
        const apiError = error instanceof ApiError ? error : new ApiError(0, String(error));
        if (isLocked(apiError) && lastGood.has(key)) {
          setStale(key, true);
          if (active) setState({ key, data: lastGood.get(key) as T, stale: true });
          return;
        }
        if (active) setState({ key, error: apiError });
      },
    );
    return () => {
      active = false;
    };
    // `load` is recreated each render; the key identifies the request.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [key, nonce]);

  const reload = useCallback(() => setNonce((value) => value + 1), []);
  const current = state.key === key;
  const showingStale = current && Boolean(state.stale);

  // While the pipeline holds the warehouse, check again every few seconds.
  useEffect(() => {
    if (!showingStale) return;
    const timer = window.setTimeout(reload, RETRY_WHILE_BUSY_MS);
    return () => window.clearTimeout(timer);
  }, [showingStale, reload, nonce]);

  return {
    data: current ? state.data : undefined,
    error: current ? state.error : undefined,
    loading: key !== null && (!current || (state.data === undefined && !state.error)),
    stale: showingStale,
    reload,
  };
}
