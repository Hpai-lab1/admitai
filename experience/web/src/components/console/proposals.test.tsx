import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import {
  GateSummary,
  ProposalDiff,
  canApproveAgain,
  escalationText,
  needsAnswer,
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
