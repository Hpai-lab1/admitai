"use client";

import { useState } from "react";
import { useMe } from "@/components/console/Shell";
import {
  CAUSE,
  GateSummary,
  type Proposal,
  ProposalDiff,
  STATUS_TONE,
  escalationText,
  canApproveAgain,
  needsAnswer,
  sortInbox,
  decisionLabel,
  type InboxOrder,
} from "@/components/console/proposals";
import { Badge, Empty, Loading, Notice, PageHeader, useApi, useRefreshWhile } from "@/components/console/ui";
import { type Draft, DraftButton } from "@/components/console/draft";
import { CatalogueConflict } from "@/components/console/CatalogueConflict";
import { ApiError, staff } from "@/lib/api";

/** College Admin only: the change reaches visitors before it is tested (PRD 8.4 tests it after). */
const URGENT_CONFIRM =
  "Publish now: visitors get this change within seconds, before it is tested. The tests still run, and if they fail the change is taken down automatically. Use this only for an urgent fix. Continue?";

export default function Approvals() {
  const me = useMe();
  const { data, error, loading, reload } = useApi<{
    items: Proposal[];
    peak_season?: boolean;
    held?: number;
  }>("/approvals");
  const history = useApi<{ items: Proposal[] }>("/approvals/history");
  // While an approved change is being tested, its result shows up here without a reload.
  useRefreshWhile(!!history.data?.items.some((p) => p.status === "evaluating"), history.reload);
  const second = useApi<{ items: Proposal[] }>("/approvals/second-reviews");
  const [reviewNotes, setReviewNotes] = useState<Record<string, string>>({});
  const [picked, setPicked] = useState<Set<string>>(new Set());
  const [answers, setAnswers] = useState<Record<string, string>>({});
  const [msg, setMsg] = useState<{ kind: "ok" | "error"; text: string } | null>(null);
  // Newest first by default, so a correction just made is on top; impact order on request.
  const [order, setOrder] = useState<InboxOrder>("newest");
  const items = sortInbox(data?.items ?? [], order);
  const batchable = items.filter((p) => !needsAnswer(p));

  const toggle = (id: string) =>
    setPicked((s) => {
      const n = new Set(s);
      if (n.has(id)) n.delete(id);
      else n.add(id);
      return n;
    });

  async function decide(ids: string[], decision: "approve" | "reject", note?: string, publishNow = false) {
    setMsg(null);
    try {
      const r = await staff<{ results: { id: string; ok: boolean; error?: string }[] }>("/approvals/decide", {
        method: "POST",
        body: JSON.stringify({ ids, decision, note: note ?? null, publish_now: publishNow }),
      });
      const ok = r.results.filter((x) => x.ok).length;
      const failed = [...new Set(r.results.filter((x) => !x.ok).map((x) => x.error))];
      setMsg({
        kind: failed.length ? "error" : "ok",
        text:
          `${ok} of ${r.results.length} ${decision}d.` +
          (decision === "approve" && ok
            ? publishNow
              ? " Live now and being tested; anything that fails the tests is taken down again."
              : " Each change goes live only after it passes the test set."
            : "") +
          (failed.length ? ` ${failed.join(" ")}` : ""),
      });
      setPicked(new Set());
      await Promise.all([reload(), history.reload()]);
    } catch (e) {
      setMsg({ kind: "error", text: e instanceof ApiError ? e.message : "That didn't work" });
    }
  }

  // Peak-season mode (PRD 8.3): only wrong-fact proposals reach experts until it is switched off.
  async function setPeak(on: boolean) {
    setMsg(null);
    try {
      await staff("/approvals/peak-season", { method: "PUT", body: JSON.stringify({ peak_season: on }) });
      setMsg({
        kind: "ok",
        text: on
          ? "Peak season is on: only wrong-fact fixes come to the inbox; the rest wait."
          : "Peak season is off: held fixes are back in the inbox.",
      });
      await reload();
    } catch (e) {
      setMsg({ kind: "error", text: e instanceof ApiError ? e.message : "That didn't work" });
    }
  }

  // PRD 8.3: each month 10% of approved fixes get a second approver (never the first one).
  async function secondReview(id: string, verdict: "agree" | "disagree") {
    setMsg(null);
    try {
      await staff(`/approvals/${id}/second-review`, {
        method: "POST",
        body: JSON.stringify({ verdict, note: reviewNotes[id]?.trim() || null }),
      });
      setMsg({
        kind: "ok",
        text:
          verdict === "agree"
            ? "Thank you: second review recorded."
            : "Recorded as a disagreement; it is counted in the quality report so the approval can be looked at again.",
      });
      await second.reload();
    } catch (e) {
      setMsg({ kind: "error", text: e instanceof ApiError ? e.message : "That didn't work" });
    }
  }

  async function rollback(id: string) {
    setMsg(null);
    try {
      await staff(`/approvals/${id}/rollback`, { method: "POST" });
      setMsg({ kind: "ok", text: "Rolled back." });
      await history.reload();
    } catch (e) {
      setMsg({ kind: "error", text: e instanceof ApiError ? e.message : "That didn't work" });
    }
  }

  return (
    <>
      <PageHeader
        title="Approval inbox"
        sub="Suggested fixes to the assistant's knowledge. Nothing changes until someone approves it, and an approved change goes live only if the test set still passes."
      >
        <label htmlFor="order" className="sr-only">
          Order
        </label>
        <select
          id="order"
          className="field w-auto"
          value={order}
          onChange={(e) => setOrder(e.target.value as InboxOrder)}
        >
          <option value="newest">Newest first</option>
          <option value="impact">Biggest impact first</option>
        </select>
      </PageHeader>
      {/* Bulk actions appear only once something is selected, and stay in view while scrolling. */}
      {picked.size > 0 && (
        <div
          role="region"
          aria-label="Selected changes"
          className="sticky top-0 z-10 mb-4 flex flex-wrap items-center gap-2 rounded-lg border border-line bg-surface px-4 py-2 shadow-sm"
        >
          <p className="mr-auto text-sm font-medium">
            {picked.size} selected
            <button className="ml-3 text-xs text-muted underline" onClick={() => setPicked(new Set())}>
              Clear
            </button>
          </p>
          <button className="btn-primary btn-sm" onClick={() => void decide([...picked], "approve")}>
            Approve {picked.size}
          </button>
          <button
            className="btn-quiet btn-sm"
            onClick={() => {
              if (window.confirm(`Reject ${picked.size} suggested change${picked.size === 1 ? "" : "s"}?`))
                void decide([...picked], "reject");
            }}
          >
            Reject
          </button>
          {me.role === "college_admin" && (
            <button
              className="btn-quiet btn-sm"
              onClick={() => {
                if (window.confirm(URGENT_CONFIRM)) void decide([...picked], "approve", undefined, true);
              }}
            >
              Publish now (urgent)
            </button>
          )}
        </div>
      )}
      {msg && <Notice kind={msg.kind}>{msg.text}</Notice>}
      {error && <Notice>{error}</Notice>}
      {data && (data.peak_season || me.role === "college_admin") && (
        <div className="mb-4 flex flex-wrap items-center gap-3 text-sm text-muted">
          <p className="mr-auto">
            {data.peak_season
              ? `Peak season: only wrong facts come here; ${data.held ?? 0} lower-priority fix${data.held === 1 ? " is" : "es are"} waiting until after the peak.`
              : "Admission season busy? Peak-season mode sends only wrong-fact fixes to reviewers."}
          </p>
          {me.role === "college_admin" && (
            <button className="btn-quiet btn-sm" onClick={() => void setPeak(!data.peak_season)}>
              {data.peak_season ? "End peak season" : "Start peak season"}
            </button>
          )}
        </div>
      )}
      {loading && !data ? (
        <Loading />
      ) : !items.length ? (
        !error && <Empty>Nothing to review.</Empty>
      ) : (
        <>
          <label className="mb-3 flex items-center gap-2 text-sm">
            <input
              type="checkbox"
              className="accent-[rgb(var(--brand))]"
              checked={!!batchable.length && batchable.every((p) => picked.has(p.id))}
              onChange={(e) => setPicked(e.target.checked ? new Set(batchable.map((p) => p.id)) : new Set())}
            />
            Select all that are ready to approve
          </label>
          <ul className="space-y-3">
            {items.map((p) => (
              <li key={p.id} className="card flex gap-4 p-4">
                <input
                  type="checkbox"
                  aria-label={`Select: ${p.title}`}
                  className="mt-1 h-4 w-4 accent-[rgb(var(--brand))]"
                  checked={picked.has(p.id)}
                  disabled={needsAnswer(p)}
                  onChange={() => toggle(p.id)}
                />
                <div className="min-w-0 flex-1">
                  <div className="flex flex-wrap items-center gap-2">
                    <Badge tone="purple">{CAUSE[p.root_cause ?? ""] ?? p.kind.replace("_", " ")}</Badge>
                    {p.department && <Badge>{p.department}</Badge>}
                    {p.overdue && <Badge tone="amber">Past the 3-day approval target</Badge>}
                    {escalationText(p) && <Badge tone="red">{escalationText(p)}</Badge>}
                    {p.evidence_count != null && (
                      <span className="text-xs text-muted">
                        {p.evidence_count} {p.evidence_count === 1 ? "conversation" : "conversations"}
                      </span>
                    )}
                  </div>
                  <p className="mt-2 font-medium">{p.title}</p>
                  <ProposalDiff p={p} />
                  <CatalogueConflict conflicts={p.catalogue_conflicts ?? []} changes={p.fee_changes ?? []} />
                  {needsAnswer(p) && (
                    <form
                      className="mt-3"
                      onSubmit={(e) => {
                        e.preventDefault();
                        void decide([p.id], "approve", answers[p.id]);
                      }}
                    >
                      <label htmlFor={`ans-${p.id}`} className="text-sm font-medium">
                        Write the answer the assistant should give
                      </label>
                      <DraftButton
                        proposalId={p.id}
                        earlier={(p.diff?.draft as Draft | undefined) ?? null}
                        onDraft={(text) => setAnswers((a) => ({ ...a, [p.id]: text }))}
                      />
                      <textarea
                        id={`ans-${p.id}`}
                        className="field mt-1 min-h-20"
                        value={answers[p.id] ?? ""}
                        onChange={(e) => setAnswers((a) => ({ ...a, [p.id]: e.target.value }))}
                      />
                      <button
                        className="btn-primary mt-2"
                        disabled={(answers[p.id] ?? "").trim().length < 15}
                      >
                        Approve with this answer
                      </button>
                      {me.role === "college_admin" && (
                        <button
                          type="button"
                          className="btn-quiet ml-2 mt-2"
                          disabled={(answers[p.id] ?? "").trim().length < 15}
                          onClick={() => {
                            if (window.confirm(URGENT_CONFIRM))
                              void decide([p.id], "approve", answers[p.id], true);
                          }}
                        >
                          Publish now (urgent)
                        </button>
                      )}
                    </form>
                  )}
                  {!!p.examples?.length && (
                    <details className="mt-2 text-sm">
                      <summary className="cursor-pointer text-muted">What visitors asked</summary>
                      <ul className="mt-1 space-y-1">
                        {p.examples.map((ex, i) => (
                          <li key={i} className="rounded bg-subtle px-2 py-1">
                            <span className="font-medium">{ex.question ?? "(question not recorded)"}</span>
                            {ex.reason && (
                              <span className="text-muted"> · marked {ex.reason.replaceAll("_", " ")}</span>
                            )}
                          </li>
                        ))}
                      </ul>
                    </details>
                  )}
                </div>
                <div className="w-28 shrink-0 text-right">
                  <p className="text-xs text-muted">Impact</p>
                  <div className="mt-1 h-2 rounded-full bg-subtle" aria-hidden>
                    <div
                      className="h-1.5 rounded-full bg-brand"
                      style={{ width: `${Math.round(p.impact_score * 100)}%` }}
                    />
                  </div>
                  <p className="mt-1 text-sm font-semibold">{Math.round(p.impact_score * 100)}</p>
                </div>
              </li>
            ))}
          </ul>
        </>
      )}

      <h2 className="mb-2 mt-8 text-lg font-semibold">Recent decisions</h2>
      {history.loading && !history.data ? (
        <Loading rows={2} />
      ) : !history.data?.items.length ? (
        <Empty>No decisions yet.</Empty>
      ) : (
        <ul className="space-y-2">
          {history.data.items.map((p) => (
            <li key={p.id} className="card flex flex-wrap items-start gap-3 p-3 text-sm">
              <Badge
                tone={
                  p.published_early && p.status === "evaluating"
                    ? "green"
                    : (STATUS_TONE[p.status ?? ""] ?? "gray")
                }
              >
                {decisionLabel(p)}
              </Badge>
              <div className="min-w-0 flex-1">
                <p className="font-medium">{p.title}</p>
                <GateSummary gate={p.eval?.gate ?? null} note={p.note} />
              </div>
              {canApproveAgain(p) && (
                <button
                  className="btn-quiet px-3 text-xs"
                  onClick={() => {
                    if (
                      window.confirm(
                        "This runs the whole test set again. It takes several minutes, and the assistant is slower until it finishes. Continue?",
                      )
                    )
                      void decide([p.id], "approve");
                  }}
                >
                  Approve again
                </button>
              )}
              {me.role === "college_admin" && p.status === "deployed" && (
                <button
                  className="btn-quiet px-3 text-xs"
                  onClick={() => {
                    // Undoing a live change is the College Admin's decision: never one click.
                    if (
                      window.confirm(
                        `Roll back "${p.title}"? Visitors go back to the answer they got before it was approved.`,
                      )
                    )
                      void rollback(p.id);
                  }}
                >
                  Roll back
                </button>
              )}
            </li>
          ))}
        </ul>
      )}

      {!!second.data?.items.length && (
        <section aria-labelledby="second-reviews" className="mt-8">
          <h2 id="second-reviews" className="mb-1 text-lg font-semibold">
            Second reviews this month
          </h2>
          <p className="mb-3 text-sm text-muted">
            A sample of fixes someone else approved. Do you agree they were right?
          </p>
          <ul className="space-y-2">
            {second.data.items.map((p) => (
              <li key={p.id} className="card space-y-2 p-3 text-sm">
                <div className="flex flex-wrap items-center gap-2">
                  <Badge tone="purple">{CAUSE[p.root_cause ?? ""] ?? p.kind.replace("_", " ")}</Badge>
                  {p.department && <Badge>{p.department}</Badge>}
                  <p className="font-medium">{p.title}</p>
                </div>
                <ProposalDiff p={p} />
                <label htmlFor={`rev-${p.id}`} className="sr-only">
                  Note for this second review
                </label>
                <input
                  id={`rev-${p.id}`}
                  className="field"
                  placeholder="Note (optional)"
                  value={reviewNotes[p.id] ?? ""}
                  onChange={(e) => setReviewNotes((n) => ({ ...n, [p.id]: e.target.value }))}
                />
                <div className="flex gap-2">
                  <button
                    className="btn-primary px-3 text-xs"
                    onClick={() => void secondReview(p.id, "agree")}
                  >
                    Agree
                  </button>
                  <button
                    className="btn-quiet px-3 text-xs"
                    onClick={() => void secondReview(p.id, "disagree")}
                  >
                    Disagree
                  </button>
                </div>
              </li>
            ))}
          </ul>
        </section>
      )}

      {me.role === "college_admin" && <Delegates onSaved={(text) => setMsg({ kind: "ok", text })} />}
    </>
  );
}

/** PRD 8.3: an overdue proposal goes to a named delegate before the College Admin. One row per
 *  department, or "default" for college-wide fixes. */
function Delegates({ onSaved }: { onSaved: (text: string) => void }) {
  const settings = useApi<{ delegates?: Record<string, string> }>("/approvals/settings");
  const [rows, setRows] = useState<[string, string][] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const shown = rows ?? Object.entries(settings.data?.delegates ?? {});
  const list: [string, string][] = shown.length ? shown : [["default", ""]];

  const edit = (i: number, col: 0 | 1, v: string) =>
    setRows(list.map((r, n) => (n === i ? (col === 0 ? [v, r[1]] : [r[0], v]) : r)));

  async function save(e: React.FormEvent) {
    e.preventDefault();
    setError(null);
    const delegates = Object.fromEntries(list.filter(([k, v]) => k.trim() && v.trim()));
    try {
      await staff("/approvals/delegates", { method: "PUT", body: JSON.stringify({ delegates }) });
      setRows(null);
      await settings.reload();
      onSaved("Delegates saved. Overdue fixes go to them before you.");
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "That didn't work");
    }
  }

  return (
    <details className="card mt-8 p-4 text-sm">
      <summary className="cursor-pointer font-semibold">Delegates for overdue fixes</summary>
      <p className="mt-2 text-muted">
        When a fix waits past the 3-working-day target it goes to the person named here, and only then to you.
        Use &ldquo;default&rdquo; for fixes that belong to no department.
      </p>
      {settings.error && <Notice>{settings.error}</Notice>}
      {error && <Notice>{error}</Notice>}
      <form className="mt-3 space-y-2" onSubmit={(e) => void save(e)}>
        {list.map(([dept, who], i) => (
          <div key={i} className="flex flex-wrap gap-2">
            <label className="sr-only" htmlFor={`dg-dept-${i}`}>
              Department
            </label>
            <input
              id={`dg-dept-${i}`}
              className="field max-w-xs"
              placeholder="Department or default"
              value={dept}
              onChange={(e) => edit(i, 0, e.target.value)}
            />
            <label className="sr-only" htmlFor={`dg-who-${i}`}>
              Delegate
            </label>
            <input
              id={`dg-who-${i}`}
              className="field max-w-xs"
              placeholder="Name or e-mail of the delegate"
              value={who}
              onChange={(e) => edit(i, 1, e.target.value)}
            />
          </div>
        ))}
        <div className="flex gap-2">
          <button type="button" className="btn-quiet" onClick={() => setRows([...list, ["", ""]])}>
            Add a department
          </button>
          <button className="btn-primary">Save delegates</button>
        </div>
      </form>
    </details>
  );
}
