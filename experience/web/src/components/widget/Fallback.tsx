import type { Strings } from "@/lib/i18n";
import type { ApprovedAnswer } from "@/lib/widget-api";

const STOP = new Set(
  "a an and are be can do does for how i in is it me my of on or the there to what when where which who why with you your".split(
    " ",
  ),
);
const words = (s: string) =>
  new Set(
    s
      .toLowerCase()
      .split(/[^\p{L}\p{N}]+/u)
      .filter((w) => w.length > 1 && !STOP.has(w)),
  );

/** The approved answers whose questions share the most words with the visitor's question. */
export function closestAnswers(q: string, items: ApprovedAnswer[], n = 3): ApprovedAnswer[] {
  const want = words(q);
  return items
    .map((it, i) => ({ it, i, score: [...words(it.question)].filter((w) => want.has(w)).length }))
    .filter((x) => x.score > 0)
    .sort((a, b) => b.score - a.score || a.i - b.i)
    .slice(0, n)
    .map((x) => x.it);
}

/** Degraded mode (PRD 10.2): the college's own approved answers while the assistant is down. */
export function Fallback({ items, t }: { items: ApprovedAnswer[]; t: Strings }) {
  if (!items.length) return null;
  return (
    <div className="mt-2 rounded-lg border border-line bg-paper p-2 text-sm">
      <p className="font-semibold">{t.fallbackTitle}</p>
      <ul className="mt-1 space-y-1">
        {items.map((it) => (
          <li key={it.question}>
            <details>
              <summary className="cursor-pointer">{it.question}</summary>
              <p className="mt-1 whitespace-pre-wrap">{it.answer}</p>
            </details>
          </li>
        ))}
      </ul>
    </div>
  );
}
