export type ModelRow = {
  model: string;
  answers: number;
  prompt_tokens?: number;
  completion_tokens?: number;
  answers_with_tokens?: number;
  /** Estimated from the platform's configured price per 1,000 tokens; null when unpriced. */
  cost_inr?: number | null;
};

const fmt = (n: number) => n.toLocaleString("en-IN");

/** Model usage per college (PRD F9.4): answers and the tokens the model gateway reported. Tokens
 *  are what a per-college price would be based on; answers saved before counting began have none. */
export function ByModel({ rows }: { rows: ModelRow[] }) {
  const priced = rows.some((r) => r.cost_inr != null);
  return (
    <figure className="card overflow-x-auto p-5">
      <figcaption className="mb-3 text-base font-semibold">Model usage</figcaption>
      <table className="w-full text-sm">
        <thead className="text-left text-xs text-muted">
          <tr>
            <th className="py-1 pr-3 font-semibold">Model</th>
            <th className="py-1 pr-3 text-right font-semibold">Answers</th>
            <th className="py-1 pr-3 text-right font-semibold">Tokens per answer</th>
            <th className="py-1 text-right font-semibold">Tokens in total</th>
            {priced && <th className="py-1 pl-3 text-right font-semibold">Estimated cost</th>}
          </tr>
        </thead>
        <tbody className="tabular-nums">
          {rows.map((r) => {
            const counted = r.answers_with_tokens ?? 0;
            const total = (r.prompt_tokens ?? 0) + (r.completion_tokens ?? 0);
            return (
              <tr key={r.model} className="border-t border-subtle">
                <td className="break-all py-1.5 pr-3">{r.model}</td>
                <td className="py-1.5 pr-3 text-right">{fmt(r.answers)}</td>
                <td className="py-1.5 pr-3 text-right">
                  {counted ? (
                    fmt(Math.round(total / counted))
                  ) : (
                    <span className="text-muted">not reported</span>
                  )}
                </td>
                <td className="py-1.5 text-right">
                  {counted ? (
                    <>
                      {fmt(total)}
                      {counted < r.answers && (
                        <span className="block text-xs text-muted">
                          for {fmt(counted)} of {fmt(r.answers)} answers
                        </span>
                      )}
                    </>
                  ) : (
                    <span className="text-muted">not reported</span>
                  )}
                </td>
                {priced && (
                  <td className="py-1.5 pl-3 text-right">
                    {r.cost_inr != null ? (
                      `₹${r.cost_inr.toLocaleString("en-IN", { maximumFractionDigits: 2 })}`
                    ) : (
                      <span className="text-muted">no price set</span>
                    )}
                  </td>
                )}
              </tr>
            );
          })}
        </tbody>
      </table>
    </figure>
  );
}
