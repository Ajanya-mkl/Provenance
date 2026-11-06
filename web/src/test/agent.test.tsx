import { fireEvent, render, screen, within } from "@testing-library/react";
import { MemoryRouter } from "react-router";
import { beforeEach, describe, expect, it } from "vitest";

import { AppRoutes, Providers } from "../App";
import { resetApiCache } from "../lib/useApi";
import { resetAgentMock } from "../mock/agentMock";
import { resetDatasetsMock } from "../mock/datasetsMock";

// A DataCo flag the fixtures have no investigation for, and one with a grounded report.
const NOT_INVESTIGATED = "risk_flag:1.2:2017-04-01:cat_cardio_equipment";
const WAIT = { timeout: 8000 };

function renderAt(path: string) {
  return render(
    <MemoryRouter initialEntries={[path]}>
      <Providers>
        <AppRoutes />
      </Providers>
    </MemoryRouter>,
  );
}

function start(variant: string) {
  sessionStorage.clear();
  localStorage.clear();
  resetApiCache();
  resetAgentMock();
  resetDatasetsMock();
  window.history.replaceState(null, "", `/?mock=${variant}`);
}

const flagPath = `/flags/${encodeURIComponent(NOT_INVESTIGATED)}`;

describe("The agent in the console (mock mode)", () => {
  beforeEach(() => start("1"));

  it("Flag detail: the button starts a run, shows live status, then a grounded report that only proposes", async () => {
    renderAt(flagPath);
    const button = await screen.findByRole("button", { name: /Investigate with agent/ }, WAIT);
    expect(screen.getByText(/The agent only proposes/)).toBeInTheDocument();

    fireEvent.click(button);
    expect(await screen.findByText(/calling its tools/, {}, WAIT)).toBeInTheDocument();

    expect(await screen.findByText("GROUNDED", { exact: false }, WAIT)).toBeInTheDocument();
    expect(screen.getByText(/Nothing has been applied; it waits for a named human/)).toBeInTheDocument();
    expect(screen.getByRole("link", { name: /Review in approvals/ })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /Investigate with agent/ })).not.toBeInTheDocument();
  }, 20_000);

  it("Flag detail: a grounded investigation shows no button", async () => {
    renderAt(`/flags/${encodeURIComponent("risk_flag:1.2:2016-01-01:cat_hockey")}`);
    expect(await screen.findByRole("link", { name: /Full report/ }, WAIT)).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /Investigate/ })).not.toBeInTheDocument();
  }, 15_000);

  it("Overview: the New dataset button and the Agent card", async () => {
    renderAt("/");
    expect(screen.getByRole("link", { name: /New dataset/ })).toHaveAttribute("href", "/datasets/upload");

    const card = screen.getByRole("region", { name: "Agent" });
    expect(await within(card).findByText("Investigations done", {}, WAIT)).toBeInTheDocument();
    expect(within(card).getByText("Grounded rate")).toBeInTheDocument();
    expect(within(card).getByText("Proposals awaiting approval")).toBeInTheDocument();
    expect(within(card).getByText(/Last agent action/)).toBeInTheDocument();
    expect(within(card).getByText("openai/gpt-oss-120b")).toBeInTheDocument();
    expect(within(card).getByRole("link", { name: /Investigations/ })).toHaveAttribute("href", "/investigations");
    expect(within(card).getByRole("link", { name: /Approvals/ })).toHaveAttribute("href", "/approvals");
  }, 15_000);

  it("Sidebar and activity: the renamed entry, and agent entries labelled as the agent", async () => {
    renderAt("/");
    const nav = screen.getByRole("navigation", { name: "Primary" });
    expect(within(nav).getByRole("link", { name: "Datasets & pipelines" })).toHaveAttribute("href", "/datasets");

    const feed = screen.getByRole("region", { name: "Recent activity" });
    expect((await within(feed).findAllByText("Agent", {}, WAIT)).length).toBeGreaterThan(0);
  }, 15_000);

  it("Datasets: the Upload dataset button and the four-step explainer", async () => {
    renderAt("/datasets");
    expect(await screen.findByRole("link", { name: /Upload dataset/ }, WAIT)).toHaveAttribute("href", "/datasets/upload");
    const steps = screen.getByRole("list", { name: "How it works" });
    expect(within(steps).getAllByRole("listitem").map((item) => item.textContent)).toEqual([
      expect.stringContaining("Upload"),
      expect.stringContaining("Map"),
      expect.stringContaining("Run"),
      expect.stringContaining("Results"),
    ]);
  }, 15_000);
});

describe("The agent's refusals (mock mode)", () => {
  it("?mock=busy: the run is refused with the busy state", async () => {
    start("busy");
    renderAt(flagPath);
    fireEvent.click(await screen.findByRole("button", { name: /Investigate with agent/ }, WAIT));
    expect(await screen.findByText("The warehouse is busy", {}, WAIT)).toBeInTheDocument();
  }, 15_000);

  it("?mock=nokey: the missing key is explained", async () => {
    start("nokey");
    renderAt(flagPath);
    fireEvent.click(await screen.findByRole("button", { name: /Investigate with agent/ }, WAIT));
    expect(await screen.findByRole("alert", {}, WAIT)).toHaveTextContent("LLM_API_KEY is not set");
  }, 15_000);
});
