import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { ByModel } from "./usage";

describe("Model usage", () => {
  it("shows tokens per answer and in total, and says when they were not reported", () => {
    render(
      <ByModel
        rows={[
          {
            model: "qwen2.5:3b",
            answers: 10,
            prompt_tokens: 3600,
            completion_tokens: 400,
            answers_with_tokens: 8,
          },
          {
            model: "older-model",
            answers: 3,
            prompt_tokens: 0,
            completion_tokens: 0,
            answers_with_tokens: 0,
          },
        ]}
      />,
    );
    expect(screen.getByText("500")).toBeInTheDocument(); // 4000 tokens over the 8 counted answers
    expect(screen.getByText("4,000")).toBeInTheDocument();
    expect(screen.getByText("for 8 of 10 answers")).toBeInTheDocument();
    expect(screen.getAllByText("not reported")).toHaveLength(2);
  });

  it("shows an estimated cost only when the platform has set a price", () => {
    const { unmount } = render(<ByModel rows={[{ model: "m", answers: 1 }]} />);
    expect(screen.queryByText("Estimated cost")).not.toBeInTheDocument();
    unmount();
    render(
      <ByModel
        rows={[
          { model: "m", answers: 1, cost_inr: 2.3 },
          { model: "n", answers: 1, cost_inr: null },
        ]}
      />,
    );
    expect(screen.getByText("Estimated cost")).toBeInTheDocument();
    expect(screen.getByText("₹2.3")).toBeInTheDocument();
    expect(screen.getByText("no price set")).toBeInTheDocument();
  });
});
