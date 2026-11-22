"use client";

import { useState } from "react";
import { CorrectionForm } from "@/components/console/CorrectionForm";
import { ReviewText, type ReviewTextState } from "@/components/console/ReviewText";
import { CorrectionStage, type Stage } from "@/components/console/CorrectionStage";
import { useMe } from "@/components/console/Shell";
import { Badge, Empty, Loading, Notice, PageHeader, useApi, useRefreshWhile } from "@/components/console/ui";
import { isAnalyticsOnly, isDepartmentRole } from "@/lib/roles";
import { shortDate } from "@/lib/time";

type Row = {
  id: string;
  conversation_id: string;
  message_id: string;
  kind: "thumb" | "rating";
  value: number;
  reason: string | null;
  comment: string | null;
  channel: string;
  trace_id: string | null;
  department: string | null;
  created_at: string;
  corrected: boolean;
  question?: string | null;
  answer?: string | null;
  text?: ReviewTextState;
  correction_stage?: Stage | null;
};

// Review queue of low-rated answers for this person's role and departments (PRD F7.4).
export default function Review() {
  const me = useMe();
  const [only, setOnly] = useState<"negative" | "all">("negative");
  const { data, error, loading, reload } = useApi<Row[]>(`/feedback?only=${only}&limit=200`);
  // A correction on its way (being turned into a proposal, or tested): follow it live.
  useRefreshWhile(
    !!data?.some((r) => ["received", "testing", "live_testing"].includes(r.correction_stage ?? "")),
    reload,
  );
  const [open, setOpen] = useState<string | null>(null);
  const [msg, setMsg] = useState<string | null>(null);

  return (
    <>
      <PageHeader
        title="Review answers"
        sub={
          isDepartmentRole(me.role)
            ? `Answers about ${me.departments.join(", ")} that visitors marked as unhelpful.`
            : "Answers visitors marked as unhelpful. Correct them so the assistant improves."
        }
      >
        <label htmlFor="only" className="sr-only">
          Show
        </label>
        <select
          id="only"
          className="field w-auto"
          value={only}
          onChange={(e) => setOnly(e.target.value as "negative" | "all")}
        >
          <option value="negative">Low-rated only</option>
          <option value="all">All feedback</option>
        </select>
      </PageHeader>
      {msg && <Notice kind="ok">{msg}</Notice>}
      {error && <Notice>{error}</Notice>}
      {loading && !data ? (
        <Loading rows={4} />
      ) : !data?.length ? (
        <Empty>No low-rated answers. 🎉</Empty>
      ) : (
        <ul className="space-y-3">
          {data.map((r) => (
            <li key={r.id} className="card p-4">
              <div className="flex flex-wrap items-center gap-2">
                <Badge tone={r.value < 0 || (r.kind === "rating" && r.value <= 2) ? "red" : "green"}>
                  {r.kind === "thumb" ? (r.value > 0 ? "👍 helpful" : "👎 not helpful") : `★ ${r.value}/5`}
                </Badge>
                {r.reason && <Badge tone="amber">{r.reason.replace(/_/g, " ")}</Badge>}
                <Badge>{r.department ?? "Admissions"}</Badge>
                <span className="ml-auto text-xs text-muted">{shortDate(r.created_at)}</span>
              </div>
              <ReviewText text={r.text} question={r.question} answer={r.answer} />
              {r.correction_stage && (
                <p className="mt-2">
                  <CorrectionStage stage={r.correction_stage} />
                </p>
              )}
              {r.comment && (
                <p className="mt-2 text-sm">
                  <span className="text-xs font-semibold uppercase tracking-wide text-muted">
                    Visitor&apos;s comment{" "}
                  </span>
                  “{r.comment}”
                </p>
              )}
              <p className="mt-2 text-xs text-muted">
                Conversation {r.conversation_id} · message {r.message_id}
              </p>
              {open === r.id ? (
                <CorrectionForm
                  conversationId={r.conversation_id}
                  messageId={r.message_id}
                  traceId={r.trace_id}
                  originalAnswer={r.answer}
                  question={r.question}
                  onCancel={() => setOpen(null)}
                  onDone={(m) => {
                    setOpen(null);
                    setMsg(m);
                    void reload();
                  }}
                />
              ) : (
                !r.corrected &&
                !isAnalyticsOnly(me.role) && (
                  <button className="btn-quiet mt-3" onClick={() => setOpen(r.id)}>
                    Correct this answer
                  </button>
                )
              )}
            </li>
          ))}
        </ul>
      )}
    </>
  );
}
