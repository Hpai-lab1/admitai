"use client";

import { useState } from "react";
import { ApiError, staff } from "@/lib/api";

type Props = {
  conversationId: string;
  messageId: string;
  originalAnswer?: string | null;
  traceId?: string | null;
  onDone: (message: string) => void;
  onCancel: () => void;
};

/** "Correct this answer" (PRD F7.3): edited answer + reason + scope. Goes to Module A as
 *  correction.submitted and appears in the approval inbox; nothing changes until it's approved. */
export function CorrectionForm({
  conversationId,
  messageId,
  originalAnswer,
  traceId,
  onDone,
  onCancel,
}: Props) {
  const [answer, setAnswer] = useState(originalAnswer ?? "");
  const [reason, setReason] = useState("");
  const [scope, setScope] = useState<"general" | "this_answer">("general");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const id = messageId.replace(/\W/g, "");

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    setBusy(true);
    setError(null);
    try {
      await staff("/corrections", {
        method: "POST",
        body: JSON.stringify({
          conversation_id: conversationId,
          message_id: messageId,
          original_answer: originalAnswer || null,
          corrected_answer: answer,
          reason,
          scope,
          trace_id: traceId ?? null,
        }),
      });
      onDone("Correction sent for approval.");
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "That didn't work");
    } finally {
      setBusy(false);
    }
  }

  return (
    <form onSubmit={submit} className="mt-3 space-y-3 rounded-lg border border-line bg-paper p-4 text-sm">
      {error && (
        <p role="alert" className="rounded bg-red-50 px-3 py-2 text-red-800">
          {error}
        </p>
      )}
      {originalAnswer && (
        <p className="text-muted">
          <span className="font-semibold">Original answer:</span> {originalAnswer}
        </p>
      )}
      <div>
        <label htmlFor={`ans-${id}`} className="label">
          Correct answer
        </label>
        <textarea
          id={`ans-${id}`}
          className="field min-h-24"
          required
          value={answer}
          onChange={(e) => setAnswer(e.target.value)}
        />
      </div>
      <div>
        <label htmlFor={`why-${id}`} className="label">
          Why is it wrong?
        </label>
        <input
          id={`why-${id}`}
          className="field"
          required
          placeholder="e.g. fee changed for 2026-27"
          value={reason}
          onChange={(e) => setReason(e.target.value)}
        />
      </div>
      <fieldset>
        <legend className="label">Apply to</legend>
        <div className="flex flex-wrap gap-4">
          <label className="flex items-center gap-2">
            <input
              type="radio"
              name={`scope-${id}`}
              checked={scope === "general"}
              onChange={() => setScope("general")}
              className="accent-[rgb(var(--brand))]"
            />
            Every similar question
          </label>
          <label className="flex items-center gap-2">
            <input
              type="radio"
              name={`scope-${id}`}
              checked={scope === "this_answer"}
              onChange={() => setScope("this_answer")}
              className="accent-[rgb(var(--brand))]"
            />
            Only this conversation
          </label>
        </div>
      </fieldset>
      <div className="flex gap-2">
        <button className="btn-primary" disabled={busy || !answer.trim() || !reason.trim()}>
          Send for approval
        </button>
        <button type="button" className="btn-ghost" onClick={onCancel}>
          Cancel
        </button>
      </div>
    </form>
  );
}
