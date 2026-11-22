"use client";

import { useState } from "react";
import { ApiError, staff } from "@/lib/api";

export type Draft = {
  text: string | null;
  reason?: string;
  sources: { title: string; page?: number | null; heading?: string | null }[];
  model?: string;
};

/** What the draft request showed: the draft's sources, or why there is no draft. */
export function DraftResult({ draft }: { draft: Draft }) {
  if (!draft.text)
    return (
      <p role="status" className="mt-1 text-sm text-muted">
        {draft.reason ?? "No draft could be made. Write the answer yourself."}
      </p>
    );
  return (
    <p role="status" className="mt-1 text-sm text-muted">
      Draft filled in below from{" "}
      {draft.sources.map((s, i) => (
        <span key={i}>
          {i > 0 && ", "}
          <span className="font-medium text-ink">{s.title}</span>
          {s.page ? ` (page ${s.page})` : ""}
        </span>
      ))}
      . It was written by the assistant: check every detail before approving.
    </p>
  );
}

/** "Draft from documents" for a proposal that still needs its answer written (Module A). */
export function DraftButton({
  proposalId,
  earlier,
  onDraft,
}: {
  proposalId: string;
  earlier?: Draft | null;
  onDraft: (text: string) => void;
}) {
  const [busy, setBusy] = useState(false);
  const [draft, setDraft] = useState<Draft | null>(earlier ?? null);
  const [error, setError] = useState<string | null>(null);

  async function ask() {
    setBusy(true);
    setError(null);
    try {
      const r = await staff<{ draft: Draft }>(`/approvals/${proposalId}/draft`, { method: "POST" });
      setDraft(r.draft);
      if (r.draft.text) onDraft(r.draft.text);
    } catch (e) {
      setError(e instanceof ApiError ? e.message : "Could not make a draft right now");
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="mt-2">
      <button type="button" className="btn-quiet" onClick={() => void ask()} disabled={busy}>
        {busy ? "Drafting from the college's documents…" : "Draft from documents"}
      </button>
      {busy && (
        <p role="status" className="mt-1 text-sm text-muted">
          This can take up to a minute.
        </p>
      )}
      {error && (
        <p role="alert" className="mt-1 text-sm text-red-800">
          {error}
        </p>
      )}
      {!busy && draft && <DraftResult draft={draft} />}
    </div>
  );
}
