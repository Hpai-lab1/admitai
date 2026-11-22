import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { DraftResult } from "./draft";

describe("Draft from documents", () => {
  it("names the documents the draft came from and asks for a check", () => {
    render(
      <DraftResult
        draft={{ text: "There is an ATM beside the main gate.", sources: [{ title: "campus.md", page: 2 }] }}
      />,
    );
    const status = screen.getByRole("status");
    expect(status).toHaveTextContent("campus.md (page 2)");
    expect(status).toHaveTextContent("check every detail before approving");
  });

  it("says why there is no draft instead of inventing one", () => {
    render(
      <DraftResult
        draft={{ text: null, reason: "No approved document covers this question.", sources: [] }}
      />,
    );
    expect(screen.getByRole("status")).toHaveTextContent("No approved document covers this question.");
  });
});
