import { Badge } from "@/components/console/ui";

/** Where a correction is on its way to visitors (Module A's improvement loop, PRD 8). */
export type Stage =
  | "sent"
  | "received"
  | "waiting_approval"
  | "testing"
  | "live"
  | "failed"
  | "rejected"
  | "rolled_back"
  | "not_used"
  | "live_testing"
  | "taken_down";

const VIEW: Record<
  Stage,
  { tone: "green" | "amber" | "red" | "purple" | "gray"; label: string; hint: string }
> = {
  sent: { tone: "green", label: "Correction sent", hint: "Sent to the knowledge team." },
  received: {
    tone: "amber",
    label: "Correction received",
    hint: "Being turned into a proposal for the Approvals inbox (a few seconds).",
  },
  waiting_approval: {
    tone: "amber",
    label: "Waiting for approval",
    hint: "In the Approvals inbox. Visitors get it after approval and testing.",
  },
  testing: {
    tone: "purple",
    label: "Being tested",
    hint: "Approved; being tested against the college's questions before it goes live.",
  },
  live: { tone: "green", label: "Live", hint: "Visitors asking this now get the corrected answer." },
  failed: {
    tone: "red",
    label: "Failed the tests",
    hint: "It would have made other answers worse, so it is not live. See Approvals.",
  },
  rejected: { tone: "red", label: "Rejected", hint: "The approver did not accept it." },
  rolled_back: { tone: "gray", label: "Rolled back", hint: "It was live and was undone." },
  live_testing: {
    tone: "green",
    label: "Live, being tested",
    hint: "Published at once by the College Admin; it is taken down if the tests fail.",
  },
  taken_down: {
    tone: "red",
    label: "Taken down",
    hint: "It was published at once, failed the tests, and was taken down. See Approvals.",
  },
  not_used: {
    tone: "gray",
    label: "Not turned into a change",
    hint: "The review of feedback did not make it a proposal.",
  },
};

export function CorrectionStage({ stage }: { stage: Stage }) {
  const v = VIEW[stage] ?? VIEW.sent;
  return (
    <span className="inline-flex items-center gap-2">
      <Badge tone={v.tone}>{v.label}</Badge>
      <span className="text-xs text-muted">{v.hint}</span>
    </span>
  );
}
