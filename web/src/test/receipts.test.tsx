import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { ReceiptScope, ReceiptText, SourcesRow } from "../components/Receipts";

describe("number receipts", () => {
  const spans = [
    { kind: "text" as const, text: "Score " },
    { kind: "number" as const, text: "0.735", tool: "get_score_breakdown" },
    { kind: "text" as const, text: " in " },
    { kind: "number" as const, text: "2017", tool: "get_flag" },
    { kind: "text" as const, text: "-Q1, was " },
    { kind: "number" as const, text: "0.512", tool: null },
  ];

  function renderReceipts() {
    render(
      <ReceiptScope>
        <ReceiptText spans={spans} />
        <SourcesRow
          tools={[
            { name: "get_flag", ok: true },
            { name: "get_score_breakdown", ok: true },
          ]}
        />
      </ReceiptScope>,
    );
  }

  it("lights the tool a focused number came from", () => {
    renderReceipts();
    const chip = screen.getByRole("button", { name: "0.735" });
    const source = screen.getByText("Score breakdown");

    expect(source.className).not.toContain("bg-accent");
    fireEvent.focus(chip);
    expect(source.className).toContain("bg-accent");
    expect(screen.getByText("From Score breakdown")).toBeInTheDocument();
    fireEvent.blur(chip);
    expect(source.className).not.toContain("bg-accent");
  });

  it("keeps quarter labels whole and marks unsourced numbers", () => {
    renderReceipts();
    expect(screen.getByRole("button", { name: "2017-Q1" })).toBeInTheDocument();
    expect(screen.getByText("No tool returned this")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "0.512" }).className).toContain("line-through");
  });
});
