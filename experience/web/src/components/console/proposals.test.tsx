import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import {
  GateSummary,
  ProposalDiff,
  canApproveAgain,
  escalationText,
  needsAnswer,
  sortInbox,
  type Proposal,
} from "./proposals";

const base: Proposal = {
  id: "p",
  kind: "curated_answer",
  department: null,
  impact_score: 0.5,
  title: "t",
  proposal: "p",
};

describe("proposals", () => {
  it("shows the old and the proposed answer side by side", () => {
    render(
      <ProposalDiff p={{ ...base, diff: { answer: "Two instalments.", old_answer: "I don't know." } }} />,
    );
    expect(screen.getByText("The assistant said")).toBeInTheDocument();
    expect(screen.getByText("Two instalments.")).toBeInTheDocument();
  });

  it("knows when a gap still needs an answer", () => {
    expect(needsAnswer({ ...base, diff: { answer: null } })).toBe(true);
    expect(needsAnswer({ ...base, diff: { answer: "x" } })).toBe(false);
    expect(needsAnswer({ ...base, kind: "document_action", diff: {} })).toBe(false);
  });

  it("offers another approval only for a change the gate rejected", () => {
    expect(canApproveAgain({ ...base, status: "failed_gate" })).toBe(true);
    expect(canApproveAgain({ ...base, status: "deployed" })).toBe(false);
    expect(canApproveAgain({ ...base, status: "pending" })).toBe(false);
  });

  it("explains why the gate blocked a change", () => {
    render(
      <GateSummary
        gate={{
          passed: false,
          reasons: ["critical case c1 no longer passes"],
          baseline: { accuracy: 1, refusal: 1, groundedness: 1, cases: 3 },
          candidate: { accuracy: 0.5, refusal: 1, groundedness: 1, cases: 3 },
        }}
      />,
    );
    expect(screen.getByText(/accuracy 100% → 50%/)).toBeInTheDocument();
    expect(screen.getByText(/blocked: critical case c1/)).toBeInTheDocument();
  });
});

describe("escalationText", () => {
  it("names who an overdue proposal now sits with", () => {
    expect(escalationText(base)).toBeNull();
    expect(escalationText({ ...base, escalation_level: 1, assigned_to: "HOD CSE" })).toBe(
      "Passed to delegate: HOD CSE",
    );
    expect(escalationText({ ...base, escalation_level: 2 })).toBe("Escalated to the College Admin");
    // older Module A replies carry only `escalated`
    expect(escalationText({ ...base, escalated: true })).toBe("Escalated to the College Admin");
  });
});

describe("approval inbox order", () => {
  const p = (id: string, impact: number, at: string) =>
    ({
      id,
      impact_score: impact,
      last_activity_at: at,
      title: id,
      kind: "x",
      proposal: "",
      department: null,
    }) as Proposal;
  const items = [p("old-big", 90, "2026-11-08T10:00:00Z"), p("new-small", 10, "2026-11-10T09:00:00Z")];

  it("puts the newest first by default, and the biggest impact first on request", () => {
    expect(sortInbox(items, "newest").map((x) => x.id)).toEqual(["new-small", "old-big"]);
    expect(sortInbox(items, "impact").map((x) => x.id)).toEqual(["old-big", "new-small"]);
  });
});

describe("decisionLabel", () => {
  it("tells an urgent fix apart from a normal approval", async () => {
    const { decisionLabel } = await import("./proposals");
    const base = { id: "p", kind: "curated_answer", title: "t" } as Parameters<typeof decisionLabel>[0];
    expect(decisionLabel({ ...base, status: "evaluating", published_early: true })).toBe(
      "live, being tested",
    );
    expect(decisionLabel({ ...base, status: "failed_gate", published_early: true, taken_down: true })).toBe(
      "taken down",
    );
    expect(decisionLabel({ ...base, status: "evaluating" })).toBe("evaluating");
  });
});
