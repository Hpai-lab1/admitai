import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { CorrectionStage } from "./CorrectionStage";

describe("where a correction is", () => {
  it("says when visitors get the corrected answer", () => {
    render(<CorrectionStage stage="live" />);
    expect(screen.getByText("Live")).toBeInTheDocument();
    expect(screen.getByText(/Visitors asking this now get the corrected answer/)).toBeInTheDocument();
  });

  it("explains each step before that", () => {
    const { rerender } = render(<CorrectionStage stage="waiting_approval" />);
    expect(screen.getByText("Waiting for approval")).toBeInTheDocument();
    rerender(<CorrectionStage stage="testing" />);
    expect(screen.getByText("Being tested")).toBeInTheDocument();
    rerender(<CorrectionStage stage="failed" />);
    expect(screen.getByText("Failed the tests")).toBeInTheDocument();
  });
});
