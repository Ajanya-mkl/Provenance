import { act, fireEvent, render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it } from "vitest";

import { ApiError } from "../lib/api";
import { resetApiCache, useApi, usePipelineBusy } from "../lib/useApi";

let calls = 0;

function load(): Promise<{ value: string }> {
  calls += 1;
  if (calls === 1) return Promise.resolve({ value: "first load" });
  return Promise.reject(new ApiError(409, "The warehouse is locked", "locked"));
}

function Probe() {
  const resource = useApi("probe", load);
  const busy = usePipelineBusy();
  return (
    <div>
      <p>{resource.data?.value ?? (resource.error ? "error panel" : "loading")}</p>
      {busy && <p>Pipeline running, showing last loaded data</p>}
      <button type="button" onClick={resource.reload}>
        reload
      </button>
    </div>
  );
}

describe("409 while the pipeline runs", () => {
  beforeEach(() => {
    calls = 0;
    resetApiCache();
  });

  it("keeps the last good data and raises the banner instead of an error", async () => {
    render(<Probe />);
    expect(await screen.findByText("first load")).toBeInTheDocument();
    expect(screen.queryByText(/Pipeline running/)).not.toBeInTheDocument();

    await act(async () => {
      fireEvent.click(screen.getByRole("button", { name: "reload" }));
    });

    expect(await screen.findByText("Pipeline running, showing last loaded data")).toBeInTheDocument();
    expect(screen.getByText("first load")).toBeInTheDocument();
    expect(screen.queryByText("error panel")).not.toBeInTheDocument();
    expect(calls).toBe(2);
  });
});
