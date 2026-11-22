export type Conflict = {
  program_id: string;
  code: string;
  name: string;
  quoted: string[];
  fees: { id: string; category: string; component: string; amount: string; period: string }[];
};

/** An official fee that approving a correction changes (after the test). */
export type FeeChange = {
  fee_id: string;
  program: string;
  code: string;
  component: string;
  category: string;
  old: string;
  new: string;
};

const inr = (a: string) => Number(a).toLocaleString("en-IN");

/** Figures a correction quotes that no fee change covers: they would not reach visitors. */
export function unmatched(conflicts: Conflict[], changes: FeeChange[]): Conflict[] {
  return conflicts
    .map((c) => ({
      ...c,
      quoted: c.quoted.filter((q) => !changes.some((ch) => ch.code === c.code && ch.new === q)),
    }))
    .filter((c) => c.quoted.length);
}

/** A correction quotes a fee the official list does not have. When the fee it replaces is clear,
 *  approving it changes the official fee (after the test), and that is what staff are told.
 *  Otherwise the correction alone would not change the chat: say so, and how to name the fee. */
export function CatalogueConflict({
  conflicts,
  changes = [],
}: {
  conflicts: Conflict[];
  changes?: FeeChange[];
}) {
  const left = unmatched(conflicts, changes);
  if (!changes.length && !left.length) return null;
  return (
    <>
      {!!changes.length && (
        <div
          role="status"
          className="mt-3 rounded-lg border border-emerald-500/60 bg-emerald-500/10 p-3 text-sm"
        >
          <p className="font-semibold">Approving this changes the official fee list:</p>
          <ul className="mt-1 list-disc pl-5">
            {changes.map((ch) => (
              <li key={ch.fee_id}>
                {ch.program}, {ch.component} ({ch.category}): {inr(ch.old)} → {inr(ch.new)}
              </li>
            ))}
          </ul>
          <p className="mt-1">Once it passes the test, visitors are told the new fee.</p>
        </div>
      )}
      {!!left.length && (
        <div role="alert" className="mt-3 rounded-lg border border-amber-500/60 bg-amber-500/10 p-3 text-sm">
          {left.map((c) => (
            <div key={c.program_id} className="mb-2 last:mb-0">
              <p className="font-semibold">
                This won&apos;t change what the assistant says about {c.name} fees.
              </p>
              <p className="mt-1">
                It says {c.quoted.map(inr).join(", ")}, but it is not clear which fee that replaces. The
                official fee list says:{" "}
                {c.fees.map((f) => `${f.component} (${f.category}) ${inr(f.amount)} ${f.period}`).join("; ")}.
                To change a fee, name it next to the amount, for example &quot;the tuition fee is
                4,00,000&quot;; approving the correction then changes the official fee.
              </p>
            </div>
          ))}
        </div>
      )}
    </>
  );
}
