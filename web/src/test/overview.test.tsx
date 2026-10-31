import { render, screen, within } from "@testing-library/react";
import { MemoryRouter } from "react-router";
import { beforeEach, describe, expect, it } from "vitest";

import { AppRoutes, Providers } from "../App";

describe("Overview in mock mode", () => {
  beforeEach(() => {
    sessionStorage.clear();
    window.history.replaceState(null, "", "/?mock=1");
  });

  it("renders KPI tiles, the chart and the activity feed from fixtures", async () => {
    render(
      <MemoryRouter initialEntries={["/"]}>
        <Providers>
          <AppRoutes />
        </Providers>
      </MemoryRouter>,
    );

    expect(screen.getByRole("heading", { level: 1, name: "Overview" })).toBeInTheDocument();
    expect(await screen.findByText("High-tier flags")).toBeInTheDocument();
    expect(await screen.findByText(/20 risk flags across 7 quarters/)).toBeInTheDocument();
    expect(screen.getByRole("img", { name: /risk flags per quarter/i })).toBeInTheDocument();
    const feed = screen.getByRole("region", { name: "Recent activity" });
    expect(await within(feed).findAllByRole("listitem")).not.toHaveLength(0);
    expect(screen.getByText(/Mock data/)).toBeInTheDocument();
    const nav = screen.getByRole("navigation", { name: "Primary" });
    expect(within(nav).getByRole("link", { name: /Approvals/ })).toBeInTheDocument();
  });
});
