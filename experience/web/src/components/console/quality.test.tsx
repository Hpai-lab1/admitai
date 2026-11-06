import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { QualityView, TopicsView } from "./quality";

describe("quality and topics dashboards", () => {
  it("say when Module A is unavailable instead of showing zeros", () => {
    render(<QualityView data={{ available: false }} />);
    render(<TopicsView data={{ available: false }} />);
    expect(screen.getAllByRole("status")).toHaveLength(2);
    expect(screen.queryByText("0%")).not.toBeInTheDocument();
  });

  it("lists unanswered questions, most asked first", () => {
    render(
      <TopicsView
        data={{ available: true, unanswered: [{ question: "Is there a swimming pool?", count: 4 }] }}
      />,
    );
    expect(screen.getByText("Is there a swimming pool?")).toBeInTheDocument();
    expect(screen.getByText("4×")).toBeInTheDocument();
  });

  it("shows acceptance per week and the latest test runs", () => {
    render(
      <QualityView
        data={{
          available: true,
          weeks: [
            {
              week: "2026-W44",
              acceptance_rate: 0.8,
              thumbs_up: 8,
              thumbs_down: 2,
              rephrase: 3,
              talk_to_human: 1,
              proposals_opened: 2,
              proposals_approved: 1,
              proposals_deployed: 1,
              regressions_caught: 0,
            },
          ],
          eval_runs: [
            {
              id: "r1",
              label: "gate",
              trigger: "proposal",
              finished_at: "2026-11-02T10:00:00Z",
              passed: true,
              accuracy: 0.96,
            },
          ],
          proposals: { approved_share: 0.5, decided: 4 },
          median_days_to_fix: 1.5,
        }}
      />,
    );
    expect(screen.getByText("2026-W44")).toBeInTheDocument();
    expect(screen.getByText("80%")).toBeInTheDocument();
    expect(screen.getByText("96%")).toBeInTheDocument();
    expect(screen.getByText("Passed")).toBeInTheDocument();
    expect(screen.getByText("1.5 d")).toBeInTheDocument();
  });
});
