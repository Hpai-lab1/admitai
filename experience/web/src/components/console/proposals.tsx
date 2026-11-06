/** Improvement proposals from Module A, as approvers see them (PRD 8.3, 13.4). */

type Example = { question: string | null; answer: string; reason: string | null; kind: string; at: string };
export type Scores = {
  accuracy: number | null;
  refusal: number | null;
  /** Share of answer claims the grounding review found supported by their sources. */
  groundedness: number | null;
  forbidden_free?: number | null;
  cases: number;
};
export type Gate = {
  passed: boolean;
  reasons: string[];
  /** Not blocking, for the approver: e.g. sentences the grounding review could not support. */
  warnings?: string[];
  baseline?: Scores;
  candidate?: Scores;
};
export type Proposal = {
  id: string;
  kind: string;
  root_cause?: string;
  department: string | null;
  impact_score: number;
  title: string;
  proposal: string;
  evidence_count?: number;
  examples?: Example[];
  diff?: Record<string, unknown>;
  status?: string;
  overdue?: boolean;
  escalated?: boolean;
  /** PRD 8.3: 0 the owning approver, 1 a named delegate, 2 the College Admin. */
  escalation_level?: number;
  assigned_to?: string | null;
  second_review?: "pending" | "agreed" | "disagreed" | null;
  decided_by?: string | null;
  note?: string | null;
  eval?: { passed: boolean | null; gate: Gate | null } | null;
};

// PRD 8.2 root causes, in plain words for approvers.
export const CAUSE: Record<string, string> = {
  knowledge_gap: "Missing knowledge",
  hallucination: "Wrong answer corrected by staff",
  outdated: "Wrong or outdated source",
  tone: "Hard to understand",
  policy: "Sensitive topic",
  catalogue_conflict: "Disagrees with the catalogue",
};

export const STATUS_TONE: Record<string, "green" | "red" | "amber" | "gray" | "purple"> = {
  deployed: "green",
  failed_gate: "red",
  evaluating: "amber",
  rejected: "gray",
  rolled_back: "gray",
  approved_manual: "purple",
};

/** A knowledge gap has no answer yet: the approver writes it, and the gate tests it. */
export const needsAnswer = (p: Proposal) => p.kind === "curated_answer" && !p.diff?.answer;

/** The gate rejected this change; an approver may send it through the tests again (PRD 8.4). */
export const canApproveAgain = (p: Proposal) => p.status === "failed_gate";

/** Who an overdue proposal now sits with (PRD 8.3: owner, then a named delegate, then the College
 *  Admin), in words for the inbox badge; null while it is still with its owner. */
export function escalationText(p: Proposal): string | null {
  const level = p.escalation_level ?? (p.escalated ? 2 : 0);
  if (level >= 2) return "Escalated to the College Admin";
  if (level === 1) return p.assigned_to ? `Passed to delegate: ${p.assigned_to}` : "Passed to a delegate";
  return null;
}

/** Side-by-side old vs new for answers; a plain description for other changes. */
export function ProposalDiff({ p }: { p: Proposal }) {
  const d = p.diff ?? {};
  if (p.kind === "curated_answer" && d.answer) {
    return (
      <div className="mt-2 grid gap-2 text-sm sm:grid-cols-2">
        <div className="rounded border border-red-200 bg-red-50 p-2">
          <p className="text-xs font-semibold text-red-800">The assistant said</p>
          <p className="mt-1 whitespace-pre-wrap">{String(d.old_answer ?? "(no answer)")}</p>
        </div>
        <div className="rounded border border-green-200 bg-green-50 p-2">
          <p className="text-xs font-semibold text-green-800">Proposed answer</p>
          <p className="mt-1 whitespace-pre-wrap">{String(d.answer)}</p>
        </div>
      </div>
    );
  }
  return <p className="mt-1 text-sm text-muted">{p.proposal}</p>;
}

const pct = (v: number | null | undefined) => (v == null ? "-" : `${Math.round(v * 100)}%`);

/** What the evaluation gate found (PRD 8.4). */
export function GateSummary({ gate, note }: { gate: Gate | null; note?: string | null }) {
  if (!gate) return note ? <p className="text-muted">{note}</p> : null;
  return (
    <p className="text-muted">
      Test set: accuracy {pct(gate.baseline?.accuracy)} → {pct(gate.candidate?.accuracy)}, refusals{" "}
      {pct(gate.baseline?.refusal)} → {pct(gate.candidate?.refusal)}, supported claims{" "}
      {pct(gate.baseline?.groundedness)} → {pct(gate.candidate?.groundedness)}
      {gate.reasons.length ? ` · blocked: ${gate.reasons.join("; ")}` : " · passed"}
      {gate.warnings?.length ? (
        <span className="mt-1 block text-amber-900">Check: {gate.warnings.join(" · ")}</span>
      ) : null}
    </p>
  );
}
