import { fireEvent, render, screen, within } from "@testing-library/react";
import { MemoryRouter } from "react-router";
import { beforeEach, describe, expect, it } from "vitest";

import { AppRoutes, Providers } from "../App";
import { resetApiCache } from "../lib/useApi";
import fixtures from "../mock/datasets.json";
import { resetDatasetsMock } from "../mock/datasetsMock";

const ids = fixtures.ids as Record<"northwind" | "clean" | "marks" | "dirty", string>;
const WAIT = { timeout: 4000 };

function renderAt(path: string) {
  return render(
    <MemoryRouter initialEntries={[path]}>
      <Providers>
        <AppRoutes />
      </Providers>
    </MemoryRouter>,
  );
}

/** The column selector in the mapping field titled `title`. */
function fieldSelect(title: string): HTMLSelectElement {
  const row = screen.getByRole("heading", { name: title }).closest("div.border-b") as HTMLElement;
  return within(row).getAllByRole("combobox")[0] as HTMLSelectElement;
}

function chooseFile(name: string, contents = "supplier,order_date,delay_days\nA,2024-01-01,1\n") {
  const input = document.querySelector<HTMLInputElement>('input[type="file"]')!;
  const file = new File([contents], name, { type: "text/csv" });
  fireEvent.change(input, { target: { files: [file] } });
}

describe("Dataset flow in mock mode", () => {
  beforeEach(() => {
    sessionStorage.clear();
    localStorage.clear();
    resetApiCache();
    resetDatasetsMock();
    window.history.replaceState(null, "", "/?mock=1");
  });

  it("Upload: checks the type, then shows the gate report for a passing and a quarantined file", async () => {
    renderAt("/datasets/upload");
    expect(await screen.findByRole("heading", { level: 1, name: "Upload a CSV" }, WAIT)).toBeInTheDocument();

    chooseFile("report.xlsx");
    expect(screen.getByRole("alert")).toHaveTextContent("not a .csv file");

    chooseFile("regional_shipments.csv");
    fireEvent.click(screen.getByRole("button", { name: /Upload and check/ }));
    expect(await screen.findByRole("progressbar", { name: "Upload progress" })).toBeInTheDocument();
    expect(await screen.findByText("Passed the quality gate", {}, WAIT)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /Review mapping/ })).toBeInTheDocument();

    chooseFile("shipments_dirty.csv");
    fireEvent.click(screen.getByRole("button", { name: /Upload and check/ }));
    expect(await screen.findByText("Quarantined by the quality gate", {}, WAIT)).toBeInTheDocument();
    expect(screen.getByText(/never reaches silver/)).toHaveTextContent(`data/quarantine/datasets/${ids.dirty}`.split("/").pop()!);
    const failed = screen.getByRole("heading", { name: "Rules that failed" }).nextElementSibling as HTMLElement;
    expect(within(failed).getAllByText("Hard").length).toBeGreaterThan(0);
    expect(within(failed).getAllByText(/^Why:/).length).toBeGreaterThan(0);
  });

  it("Mapping review: an invalid edit shows the validator's message; a valid edit and an approve go through", async () => {
    renderAt(`/datasets/${ids.clean}/mapping`);
    expect(await screen.findByText(/Proposal v1/, {}, WAIT)).toBeInTheDocument();
    expect(screen.getByText(/Decided by: Heuristic/)).toBeInTheDocument();
    expect(screen.getByText(/Mappable: yes/)).toBeInTheDocument();
    fireEvent.change(screen.getByLabelText("Reviewer name (required)"), { target: { value: "AJANYA" } });

    // An entity holding prices is refused by the validator.
    fireEvent.change(fieldSelect("Entity"), { target: { value: "unit_price" } });
    fireEvent.click(screen.getByRole("button", { name: /Save edit/ }));
    expect(await screen.findByRole("alert", {}, WAIT)).toHaveTextContent("holds float values");

    // Undo it, then a valid edit: drop the country column.
    fireEvent.click(screen.getByRole("button", { name: "Undo changes" }));
    expect(fieldSelect("Country").value).toBe("destination_country");
    fireEvent.change(fieldSelect("Country"), { target: { value: "" } });
    fireEvent.click(screen.getByRole("button", { name: /Save edit/ }));
    expect(await screen.findByText(/Proposal v2/, {}, WAIT)).toBeInTheDocument();
    expect(screen.getByText(/Decided by: Human-edited/)).toBeInTheDocument();
    expect(screen.getByText(/no model confidence/)).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: /Approve/ }));
    expect(await screen.findByText(/Approved by AJANYA/, {}, WAIT)).toBeInTheDocument();
    expect(screen.getByRole("link", { name: /Run scoring/ })).toBeInTheDocument();
  }, 20_000);

  it("Mapping review: the student-marks proposal came from the agent and cannot be approved", async () => {
    renderAt(`/datasets/${ids.marks}/mapping`);
    expect(await screen.findByText(/Decided by: Agent/, {}, WAIT)).toBeInTheDocument();
    expect(screen.getByText(/Mappable: no/)).toBeInTheDocument();
    expect(screen.getByText(/The agent was asked because/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /Approve/ })).toBeDisabled();
  });

  it("Results: KPIs, the excluded-factors panel and the comparability note", async () => {
    renderAt(`/datasets/${ids.northwind}/results`);
    expect(await screen.findByText("Scores are comparable within this dataset only.", {}, WAIT)).toBeInTheDocument();
    expect(screen.getByText("Entities scored")).toBeInTheDocument();
    const factors = screen.getByRole("region", { name: "Factors and weights" });
    expect(within(factors).getByText(/1 factor left out/)).toBeInTheDocument();
    expect(within(factors).getByText(/no country column/)).toBeInTheDocument();
    expect(within(factors).getByText("0.417")).toBeInTheDocument();
    expect(within(factors).getByText("0.583")).toBeInTheDocument();
    expect(screen.getByRole("img", { name: /risk score per quarter/i })).toBeInTheDocument();
    expect(screen.getByText("1.2-ds1")).toBeInTheDocument();
    expect(screen.getByRole("region", { name: "Rejected rows" })).toHaveTextContent("entity is empty");
  });

  it("Run: the busy variant explains that another writer holds the warehouse", async () => {
    window.history.replaceState(null, "", "/?mock=busy");
    renderAt(`/datasets/${ids.northwind}/run`);
    fireEvent.click(await screen.findByRole("button", { name: /Start run/ }, WAIT));
    expect(await screen.findByText("The warehouse is busy", {}, WAIT)).toBeInTheDocument();
  }, 10_000);
});
