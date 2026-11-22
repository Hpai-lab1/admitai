"use client";

import Link from "next/link";
import { Badge, useApi } from "@/components/console/ui";

export type TodayItem = {
  kind: string;
  count: number | null;
  title: string;
  href: string;
  priority: "urgent" | "high" | "normal";
  next_due?: string | null;
};

const TONE = { urgent: "red", high: "amber", normal: "gray" } as const;
const LABEL = { urgent: "Urgent", high: "Soon", normal: "Today" } as const;

/** "Needs you today": the few things to do first, most urgent first (GET /today). */
export function TodayView({ items }: { items: TodayItem[] }) {
  return (
    <section aria-labelledby="today-heading" className="card mb-6 p-4">
      <h2 id="today-heading" className="text-sm font-semibold text-ink">
        Needs you today
      </h2>
      {items.length === 0 ? (
        <p className="mt-2 text-sm" role="status">
          Nothing is waiting for you. New requests will appear here.
        </p>
      ) : (
        <ul className="mt-2 divide-y divide-line">
          {items.map((i) => (
            <li key={i.kind} className="flex items-center gap-3 py-2">
              <Badge tone={TONE[i.priority]}>{LABEL[i.priority]}</Badge>
              {i.count === null ? (
                <span className="text-sm text-muted" role="status">
                  {i.title}
                </span>
              ) : (
                <Link href={i.href} className="text-sm font-semibold hover:underline">
                  {i.title}
                </Link>
              )}
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}

export function Today() {
  const { data, error } = useApi<{ items: TodayItem[] }>("/today");
  if (error || !data) return null; // the page below still works; this is only a summary
  return <TodayView items={data.items} />;
}

/** What is waiting per console section, for the sidebar: { "/console/approvals": {n, urgent} }. */
export function countsByPage(items: TodayItem[]): Record<string, { n: number; urgent: boolean }> {
  const out: Record<string, { n: number; urgent: boolean }> = {};
  for (const i of items) {
    if (i.count == null) continue;
    const page = (i.href.split("?")[0] ?? "").replace(/\/$/, "") || "/console";
    const cur = out[page] ?? { n: 0, urgent: false };
    // The same task can be in two lines (overdue and unclaimed): show the larger, not the sum.
    out[page] = { n: Math.max(cur.n, i.count), urgent: cur.urgent || i.priority === "urgent" };
  }
  return out;
}
