import { useEffect, useState } from "react";

import { ApiError } from "./api";

const POLL_MS = 1000;

/** Poll a background run until it finishes. A locked read (409) just polls again. */
export function useRun<T extends { run_id: string; status: string }>(
  runId: string | null,
  load: (runId: string) => Promise<T>,
) {
  const [run, setRun] = useState<T | null>(null);
  const [error, setError] = useState<ApiError | null>(null);
  useEffect(() => {
    if (!runId) return;
    let active = true;
    let timer: number | undefined;
    const poll = async () => {
      try {
        const next = await load(runId);
        if (!active) return;
        setRun(next);
        setError(null);
        if (next.status !== "succeeded" && next.status !== "failed") {
          timer = window.setTimeout(poll, POLL_MS);
        }
      } catch (caught) {
        if (!active) return;
        const apiError = caught instanceof ApiError ? caught : new ApiError(0, String(caught));
        setError(apiError);
        if (apiError.status === 409) timer = window.setTimeout(poll, POLL_MS);
      }
    };
    poll();
    return () => {
      active = false;
      window.clearTimeout(timer);
    };
    // `load` is a stable api function; the run id identifies the request.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [runId]);
  return { run: run?.run_id === runId ? run : null, error };
}
