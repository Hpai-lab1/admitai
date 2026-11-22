/** What a rating is about: the visitor's question and the assistant's answer (from Module B,
 *  with phone numbers and e-mail addresses masked), or why they cannot be shown. */
export type ReviewTextState = "found" | "missing" | "unavailable" | "conversation";

export function ReviewText({
  text,
  question,
  answer,
}: {
  text?: ReviewTextState;
  question?: string | null;
  answer?: string | null;
}) {
  if (text === "conversation")
    return <p className="mt-2 text-sm text-muted">A rating of the whole chat, not of one answer.</p>;
  if (text === "unavailable")
    return (
      <p role="status" className="mt-2 text-sm text-muted">
        The question and answer cannot be loaded right now (the assistant service is not reachable). Try again
        in a moment.
      </p>
    );
  if (text !== "found")
    return (
      <p className="mt-2 text-sm text-muted">
        No stored conversation for this rating: it was deleted at the visitor&apos;s request, or it is a demo
        record.
      </p>
    );
  return (
    <dl className="mt-3 space-y-2 text-sm">
      <div>
        <dt className="text-xs font-semibold uppercase tracking-wide text-muted">Visitor asked</dt>
        <dd className="mt-0.5">{question || "(no question recorded)"}</dd>
      </div>
      <div>
        <dt className="text-xs font-semibold uppercase tracking-wide text-muted">Assistant answered</dt>
        <dd className="mt-0.5 whitespace-pre-wrap rounded-lg bg-subtle px-3 py-2">{answer}</dd>
      </div>
    </dl>
  );
}
