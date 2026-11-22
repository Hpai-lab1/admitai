/** Quality over time (PRD F9.3) and unanswered questions (PRD F9.2), from Module A through
 *  Module C's /analytics/quality and /analytics/topics. `available: false` means Module A did not
 *  respond: the dashboard says so instead of showing zeros. */

export type QualityWeek = {
  week: string;
  acceptance_rate: number | null;
  thumbs_up: number;
  thumbs_down: number;
  rephrase: number;
  talk_to_human: number;
  proposals_opened: number;
  proposals_approved: number;
  proposals_deployed: number;
  regressions_caught: number;
};

export type EvalRun = {
  id: string;
  label: string | null;
  trigger: string | null;
  finished_at: string;
  passed: boolean | null;
  accuracy?: number | null;
  groundedness?: number | null;
  cases?: number | null;
};

export type QualityReport =
  | { available: false }
  | {
      available: true;
      weeks: QualityWeek[];
      eval_runs?: EvalRun[];
      proposals?: { approved_share: number | null; decided: number };
      median_days_to_fix?: number | null;
      second_review?: { pending?: number; agreed?: number; disagreed?: number };
    };

export type IntentRow = { intent: string; current: number; previous: number };
export type TrendingRow = { question: string; current: number; previous: number };
type Asked = { intents?: IntentRow[]; trending?: TrendingRow[] };

export type TopicsReport =
  | ({ available: false } & Asked)
  | ({ available: true; unanswered: { question: string | null; count: number }[] } & Asked);

const pct = (v: number | null | undefined) => (v == null ? "—" : `${Math.round(v * 100)}%`);

function Unavailable({ what }: { what: string }) {
  return (
    <p role="status" className="text-sm text-muted">
      {what} is not available right now: the knowledge service did not respond.
    </p>
  );
}

const change = (current: number, previous: number) =>
  previous === 0
    ? "new"
    : current === previous
      ? "same"
      : `${current > previous ? "+" : ""}${current - previous}`;

/** What visitors asked about this week (PRD F9.2). Compared with the week before. */
function AskedView({ data }: { data: TopicsReport | null }) {
  if (!data) return null;
  if (!data.intents) {
    return (
      <figure className="card p-5">
        <figcaption className="mb-1 text-base font-semibold">Top intents and trending questions</figcaption>
        <p role="status" className="text-sm text-muted">
          These are not available right now: the assistant service did not respond.
        </p>
      </figure>
    );
  }
  return (
    <figure className="card p-5">
      <figcaption className="mb-1 text-base font-semibold">Top intents and trending questions</figcaption>
      <p className="mb-3 text-xs text-muted">
        This week, compared with the week before. A question appears once two or more conversations ask it;
        personal details are masked.
      </p>
      {data.intents.length === 0 ? (
        <p className="text-sm text-muted">No questions this week.</p>
      ) : (
        <ol aria-label="Top intents" className="space-y-1 text-sm">
          {data.intents.map((r) => (
            <li key={r.intent} className="flex gap-3">
              <span className="min-w-0 flex-1 capitalize">{r.intent.replaceAll("_", " ")}</span>
              <span className="font-semibold tabular-nums">{r.current}</span>
              <span className="w-12 text-right text-xs text-muted">{change(r.current, r.previous)}</span>
            </li>
          ))}
        </ol>
      )}
      <h3 className="mb-1 mt-4 text-sm font-semibold">Trending questions</h3>
      {!data.trending?.length ? (
        <p className="text-sm text-muted">Nothing is trending this week.</p>
      ) : (
        <ol aria-label="Trending questions" className="space-y-1 text-sm">
          {data.trending.map((r) => (
            <li key={r.question} className="flex gap-3">
              <span className="min-w-0 flex-1">{r.question}</span>
              <span className="font-semibold tabular-nums">{r.current}×</span>
              <span className="w-12 text-right text-xs text-muted">{change(r.current, r.previous)}</span>
            </li>
          ))}
        </ol>
      )}
    </figure>
  );
}

export function TopicsView({ data }: { data: TopicsReport | null }) {
  return (
    <>
      <AskedView data={data} />
      <UnansweredView data={data} />
    </>
  );
}

function UnansweredView({ data }: { data: TopicsReport | null }) {
  return (
    <figure className="card p-5">
      <figcaption className="mb-1 text-base font-semibold">
        Questions the assistant could not answer
      </figcaption>
      <p className="mb-3 text-xs text-muted">This week, most asked first; personal details are masked.</p>
      {!data ? (
        <p className="text-sm text-muted">Loading…</p>
      ) : !data.available ? (
        <Unavailable what="This list" />
      ) : data.unanswered.length === 0 ? (
        <p className="text-sm text-muted">None this week.</p>
      ) : (
        <ol className="space-y-1 text-sm">
          {data.unanswered.map((q, i) => (
            <li key={i} className="flex gap-3">
              <span className="min-w-0 flex-1">{q.question ?? "(question not recorded)"}</span>
              <span className="font-semibold tabular-nums">{q.count}×</span>
            </li>
          ))}
        </ol>
      )}
    </figure>
  );
}

export function QualityView({ data }: { data: QualityReport | null }) {
  if (!data) return <p className="text-sm text-muted">Loading…</p>;
  if (!data.available)
    return (
      <div className="card p-5">
        <Unavailable what="The quality report" />
      </div>
    );
  const runs = (data.eval_runs ?? []).slice(-5).reverse();
  const weeks = data.weeks.slice(-8);
  const sr = data.second_review ?? {};
  return (
    <div className="space-y-4">
      <div className="grid gap-4 sm:grid-cols-3">
        <div className="card p-4">
          <p className="text-xs font-semibold text-muted">Fixes approved</p>
          <p className="mt-1 text-2xl font-semibold tabular-nums text-ink">
            {pct(data.proposals?.approved_share)}
          </p>
          <p className="mt-1 text-xs text-muted">of {data.proposals?.decided ?? 0} decided</p>
        </div>
        <div className="card p-4">
          <p className="text-xs font-semibold text-muted">Median time to fix</p>
          <p className="mt-1 text-2xl font-semibold tabular-nums text-ink">
            {data.median_days_to_fix == null ? "—" : `${data.median_days_to_fix} d`}
          </p>
        </div>
        <div className="card p-4">
          <p className="text-xs font-semibold text-muted">Second reviews</p>
          <p className="mt-1 text-2xl font-semibold tabular-nums text-ink">{sr.pending ?? 0} waiting</p>
          <p className="mt-1 text-xs text-muted">
            {sr.agreed ?? 0} agreed · {sr.disagreed ?? 0} disagreed
          </p>
        </div>
      </div>
      <figure className="card overflow-x-auto p-5">
        <figcaption className="mb-3 text-base font-semibold">Week by week</figcaption>
        {weeks.length === 0 ? (
          <p className="text-sm text-muted">No data yet.</p>
        ) : (
          <table className="w-full text-sm">
            <thead className="text-left text-xs text-muted">
              <tr>
                <th className="py-1 pr-3 font-semibold">Week</th>
                <th className="py-1 pr-3 text-right font-semibold">Acceptance</th>
                <th className="py-1 pr-3 text-right font-semibold">Rephrased</th>
                <th className="py-1 pr-3 text-right font-semibold">Asked for a person</th>
                <th className="py-1 pr-3 text-right font-semibold">Fixes opened</th>
                <th className="py-1 pr-3 text-right font-semibold">Live</th>
                <th className="py-1 text-right font-semibold">Regressions caught</th>
              </tr>
            </thead>
            <tbody className="tabular-nums">
              {weeks.map((w) => (
                <tr key={w.week} className="border-t border-subtle">
                  <td className="py-1.5 pr-3">{w.week}</td>
                  <td className="py-1.5 pr-3 text-right">{pct(w.acceptance_rate)}</td>
                  <td className="py-1.5 pr-3 text-right">{w.rephrase}</td>
                  <td className="py-1.5 pr-3 text-right">{w.talk_to_human}</td>
                  <td className="py-1.5 pr-3 text-right">{w.proposals_opened}</td>
                  <td className="py-1.5 pr-3 text-right">{w.proposals_deployed}</td>
                  <td className="py-1.5 text-right">{w.regressions_caught}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </figure>
      <figure className="card overflow-x-auto p-5">
        <figcaption className="mb-3 text-base font-semibold">Test-set score per release</figcaption>
        {runs.length === 0 ? (
          <p className="text-sm text-muted">No test runs yet.</p>
        ) : (
          <table className="w-full text-sm">
            <thead className="text-left text-xs text-muted">
              <tr>
                <th className="py-1 pr-3 font-semibold">Finished</th>
                <th className="py-1 pr-3 font-semibold">Run</th>
                <th className="py-1 pr-3 text-right font-semibold">Accuracy</th>
                <th className="py-1 pr-3 text-right font-semibold">Supported claims</th>
                <th className="py-1 text-right font-semibold">Result</th>
              </tr>
            </thead>
            <tbody className="tabular-nums">
              {runs.map((r) => (
                <tr key={r.id} className="border-t border-subtle">
                  <td className="py-1.5 pr-3">{r.finished_at.slice(0, 10)}</td>
                  <td className="py-1.5 pr-3">{r.label ?? r.trigger ?? "—"}</td>
                  <td className="py-1.5 pr-3 text-right">{pct(r.accuracy)}</td>
                  <td className="py-1.5 pr-3 text-right">{pct(r.groundedness)}</td>
                  <td className="py-1.5 text-right">
                    {r.passed == null ? "—" : r.passed ? "Passed" : "Failed"}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </figure>
    </div>
  );
}
