"use client";

import { useState } from "react";
import { useMe } from "@/components/console/Shell";
import { type QualityReport, QualityView, type TopicsReport, TopicsView } from "@/components/console/quality";
import { Loading, Notice, PageHeader, useApi } from "@/components/console/ui";
import { ByModel, type ModelRow } from "@/components/console/usage";
import { meets, type Target } from "@/components/console/targets";

type Overview = {
  period_days: number;
  sessions: { total: number; voice: number };
  conversations: {
    total: number;
    qualifying: number;
    single_message: number;
    resolved: number;
    handed_off: number;
    abandoned: number;
    resolution_rate: number | null;
    handoff_rate: number | null;
    lead_capture_rate: number | null;
    abandonment_rate: number | null;
    voice_share: number | null;
    whatsapp_share?: number | null;
  };
  /** Module B's answer outcomes by channel; null when the assistant service did not respond. */
  assistant?: { by_channel: ChannelRow[]; by_model?: ModelRow[] } | null;
  feedback: {
    helpful: number;
    not_helpful: number;
    helpful_rate: number | null;
    csat: number | null;
    ratings: number;
    reasons: { reason: string; n: number }[];
  };
  handoffs?: {
    open: number;
    overdue: number;
    resolved: number;
    sla_met_rate: number | null;
    median_hours: number | null;
    by_queue: { queue: string; n: number }[];
  };
  corrections?: { submitted: number; accepted: number };
  leads?: {
    total: number;
    hot: number;
    warm: number;
    cold: number;
    via_guardian: number;
    capture_rate: number | null;
    funnel: Record<string, number>;
    by_source: { source: string; n: number; applied: number }[];
  };
};

type ChannelRow = {
  channel: string;
  answers: number;
  answered: number;
  partial: number;
  no_answer: number;
  handoff: number;
  blocked: number;
  latency_p95_ms: number | null;
};

const CHANNEL = { web_chat: "Web chat", web_voice: "Voice", whatsapp: "WhatsApp", phone: "Phone" };

/** PRD F9.4: which model answered. GPU use and cost need the lab's metrics. */
function ByChannel({ rows }: { rows: ChannelRow[] }) {
  return (
    <figure className="overflow-x-auto rounded-lg border border-line bg-surface p-4">
      <figcaption className="mb-3 text-base font-semibold">
        Assistant answers by channel
        <span className="ml-2 text-xs font-normal text-muted">first sentence target: 5 s p95 for chat</span>
      </figcaption>
      {rows.length === 0 ? (
        <p className="text-sm text-muted">No answers yet.</p>
      ) : (
        <table className="w-full text-sm">
          <thead className="text-left text-xs text-muted">
            <tr>
              <th className="py-1 pr-3 font-medium">Channel</th>
              <th className="py-1 pr-3 text-right font-medium">Answers</th>
              <th className="py-1 pr-3 text-right font-medium">Answered</th>
              <th className="py-1 pr-3 text-right font-medium">Not known</th>
              <th className="py-1 pr-3 text-right font-medium">To a person</th>
              <th className="py-1 pr-3 text-right font-medium">Blocked</th>
              <th className="py-1 text-right font-medium">First sentence p95</th>
            </tr>
          </thead>
          <tbody className="tabular-nums">
            {rows.map((r) => (
              <tr key={r.channel} className="border-t border-subtle">
                <td className="py-1.5 pr-3">{CHANNEL[r.channel as keyof typeof CHANNEL] ?? r.channel}</td>
                <td className="py-1.5 pr-3 text-right">{r.answers}</td>
                <td className="py-1.5 pr-3 text-right">
                  {pct(r.answers ? (r.answered + r.partial) / r.answers : null)}
                </td>
                <td className="py-1.5 pr-3 text-right">{r.no_answer}</td>
                <td className="py-1.5 pr-3 text-right">{r.handoff}</td>
                <td className="py-1.5 pr-3 text-right">{r.blocked}</td>
                <td
                  className={`py-1.5 text-right ${
                    r.channel === "web_chat" && (r.latency_p95_ms ?? 0) > 5000
                      ? "font-semibold text-warn"
                      : ""
                  }`}
                >
                  {r.latency_p95_ms == null ? "—" : `${(r.latency_p95_ms / 1000).toFixed(1)} s`}
                  {r.channel === "web_chat" && (r.latency_p95_ms ?? 0) > 5000 && (
                    <span className="sr-only"> (over the 5 s target)</span>
                  )}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </figure>
  );
}

const pct = (v: number | null | undefined) => (v == null ? "—" : `${Math.round(v * 100)}%`);

function Tile({
  label,
  value,
  hint,
  raw,
  target,
}: {
  label: string;
  value: string | number;
  hint?: string;
  raw?: number | null;
  target?: Target;
}) {
  const ok = target ? meets(raw, target) : null;
  return (
    <div className="rounded-lg border border-line bg-surface p-4">
      <p className="text-sm text-muted">{label}</p>
      <p className="mt-1 text-2xl font-semibold tabular-nums text-ink">{value}</p>
      {target && (
        <p className="mt-1 text-xs">
          <span className="text-muted">Target {target.text}</span>
          {ok != null && (
            <span className={`ml-2 font-semibold ${ok ? "text-ok" : "text-warn"}`}>
              {ok ? "Met" : "Below target"}
            </span>
          )}
        </p>
      )}
      {hint && <p className="mt-1 text-xs text-muted">{hint}</p>}
    </div>
  );
}

/** Secondary figures as a two-column list: label, value, and what it means. */
function Facts({ rows }: { rows: [string, string, string?][] }) {
  return (
    <dl className="divide-y divide-line rounded-lg border border-line bg-surface text-sm">
      {rows.map(([k, v, note]) => (
        <div
          key={k}
          className="grid grid-cols-[minmax(0,1fr)_auto] items-baseline gap-x-4 gap-y-0.5 px-4 py-2 sm:grid-cols-[minmax(0,14rem)_6rem_minmax(0,1fr)]"
        >
          <dt className="text-muted">{k}</dt>
          <dd className="text-right font-medium tabular-nums">{v}</dd>
          <dd className="col-span-2 text-xs text-muted sm:col-span-1">{note}</dd>
        </div>
      ))}
    </dl>
  );
}

function Bars({ title, rows }: { title: string; rows: [string, number][] }) {
  const max = Math.max(1, ...rows.map(([, n]) => n));
  return (
    <figure className="rounded-lg border border-line bg-surface p-4">
      <figcaption className="mb-3 text-base font-semibold">{title}</figcaption>
      {rows.length === 0 ? (
        <p className="text-sm text-muted">No data yet.</p>
      ) : (
        <ul className="space-y-2">
          {rows.map(([label, n]) => (
            <li key={label} className="grid grid-cols-[8rem_1fr_2.5rem] items-center gap-3 text-sm">
              <span className="truncate capitalize">{label.replace(/_/g, " ")}</span>
              <span className="h-2 rounded-full bg-subtle" aria-hidden>
                <span className="block h-2 rounded-full bg-brand" style={{ width: `${(n / max) * 100}%` }} />
              </span>
              <span className="text-right font-semibold tabular-nums">{n}</span>
            </li>
          ))}
        </ul>
      )}
    </figure>
  );
}

export default function Analytics() {
  const [days, setDays] = useState(30);
  const { data: a, error, loading } = useApi<Overview>(`/analytics/overview?days=${days}`);
  const me = useMe();
  // F9.2 and F9.3 come from Module A; only roles that review answers see them.
  const reviewer = ["college_admin", "admission_officer", "management"].includes(me.role);
  const quality = useApi<QualityReport>(reviewer ? "/analytics/quality?weeks=12" : null);
  const topics = useApi<TopicsReport>(reviewer ? "/analytics/topics" : null);

  return (
    <>
      <PageHeader title="Analytics" sub="Numbers cover only what your role can see.">
        <label htmlFor="days" className="sr-only">
          Period
        </label>
        <select
          id="days"
          className="field w-auto"
          value={days}
          onChange={(e) => setDays(Number(e.target.value))}
        >
          <option value={7}>Last 7 days</option>
          <option value={30}>Last 30 days</option>
          <option value={90}>Last 90 days</option>
        </select>
      </PageHeader>
      {error && <Notice>{error}</Notice>}
      {loading && !a ? (
        <Loading />
      ) : (
        a && (
          <div className="space-y-6">
            <section aria-labelledby="head-h">
              <h2 id="head-h" className="sr-only">
                Against the PRD targets
              </h2>
              <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
                <Tile
                  label="Resolution rate"
                  value={pct(a.conversations.resolution_rate)}
                  raw={a.conversations.resolution_rate}
                  target={{ min: 0.8, text: "≥ 80%" }}
                  hint="Answered without a person, not rated unhelpful, no repeat at the end"
                />
                {a.leads && (
                  <Tile
                    label="Lead capture"
                    value={pct(a.conversations.lead_capture_rate)}
                    raw={a.conversations.lead_capture_rate}
                    target={{ min: 0.25, text: "≥ 25%" }}
                    hint="Conversations of 2+ messages that left a lead"
                  />
                )}
                <Tile
                  label="Answer acceptance"
                  value={pct(a.feedback.helpful_rate)}
                  hint={`${a.feedback.helpful} helpful · ${a.feedback.not_helpful} not helpful`}
                />
                {a.handoffs && (
                  <Tile
                    label="Open tasks"
                    value={a.handoffs.open}
                    hint={a.handoffs.overdue ? `${a.handoffs.overdue} overdue` : "None overdue"}
                  />
                )}
              </div>
            </section>
            <section aria-labelledby="detail-h">
              <h2 id="detail-h" className="mb-3 text-base font-semibold">
                Conversations and follow-up
              </h2>
              <Facts
                rows={[
                  [
                    "Conversations",
                    String(a.conversations.total),
                    `${a.conversations.qualifying} with 2+ messages, ${a.conversations.single_message} single message`,
                  ],
                  [
                    "Handed to a person",
                    pct(a.conversations.handoff_rate),
                    `${a.conversations.handed_off} conversations`,
                  ],
                  [
                    "Abandoned",
                    pct(a.conversations.abandonment_rate),
                    "Left within 10 min of an unrated answer",
                  ],
                  [
                    "Chat ratings, 4–5 stars",
                    pct(a.feedback.csat),
                    a.feedback.ratings ? `${a.feedback.ratings} ratings` : "No ratings yet",
                  ],
                  [
                    "Voice share",
                    pct(a.conversations.voice_share),
                    `${a.sessions.total} visitor sessions${
                      a.conversations.whatsapp_share != null
                        ? `, WhatsApp ${pct(a.conversations.whatsapp_share)}`
                        : ""
                    }`,
                  ],
                  ...(a.handoffs
                    ? ([
                        [
                          "Tasks answered on time",
                          pct(a.handoffs.sla_met_rate),
                          a.handoffs.sla_met_rate == null
                            ? "No task was resolved in this period"
                            : a.handoffs.median_hours != null
                              ? `Median ${a.handoffs.median_hours} h to resolve`
                              : "",
                        ],
                      ] as [string, string, string][])
                    : []),
                  ...(a.leads
                    ? ([
                        [
                          "Leads",
                          String(a.leads.total),
                          `${a.leads.hot} hot, ${a.leads.warm} warm, ${a.leads.cold} cold; ${a.leads.via_guardian} via a parent`,
                        ],
                      ] as [string, string, string][])
                    : []),
                ]}
              />
            </section>
            {a.assistant === null && (
              <Notice>
                WhatsApp conversations and answer outcomes are missing: the assistant service did not respond.
                The other numbers cover the website only.
              </Notice>
            )}
            {a.assistant && <ByChannel rows={a.assistant.by_channel} />}
            {!!a.assistant?.by_model?.length && <ByModel rows={a.assistant.by_model} />}
            <div className="grid gap-6 lg:grid-cols-2">
              {a.leads && (
                <Bars
                  title="Admissions funnel"
                  rows={(["new", "contacted", "applied", "admitted", "lost"] as const).map((s) => [
                    s,
                    a.leads?.funnel[s] ?? 0,
                  ])}
                />
              )}
              {a.leads && (
                <Bars title="Leads by source" rows={a.leads.by_source.map((s) => [s.source, s.n])} />
              )}
              <Bars
                title="Why answers were marked unhelpful"
                rows={a.feedback.reasons.map((r) => [r.reason, r.n])}
              />
              {a.handoffs && (
                <Bars title="Tasks by queue" rows={a.handoffs.by_queue.map((q) => [q.queue, q.n])} />
              )}
              {reviewer && <TopicsView data={topics.data} />}
            </div>
            {reviewer && (
              <section aria-labelledby="quality-h">
                <h2 id="quality-h" className="mb-3 text-base font-semibold">
                  Quality and improvement
                </h2>
                {quality.error ? <Notice>{quality.error}</Notice> : <QualityView data={quality.data} />}
              </section>
            )}
          </div>
        )
      )}
    </>
  );
}
