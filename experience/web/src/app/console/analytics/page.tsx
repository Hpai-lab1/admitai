"use client";

import { useState } from "react";
import { useMe } from "@/components/console/Shell";
import { type QualityReport, QualityView, type TopicsReport, TopicsView } from "@/components/console/quality";
import { Loading, Notice, PageHeader, useApi } from "@/components/console/ui";

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
  assistant?: { by_channel: ChannelRow[] } | null;
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

function ByChannel({ rows }: { rows: ChannelRow[] }) {
  return (
    <figure className="card overflow-x-auto p-5">
      <figcaption className="mb-3 font-bold">Assistant answers by channel</figcaption>
      {rows.length === 0 ? (
        <p className="text-sm text-muted">No answers yet.</p>
      ) : (
        <table className="w-full text-sm">
          <thead className="text-left text-xs uppercase tracking-wide text-muted">
            <tr>
              <th className="py-1 pr-3 font-semibold">Channel</th>
              <th className="py-1 pr-3 text-right font-semibold">Answers</th>
              <th className="py-1 pr-3 text-right font-semibold">Answered</th>
              <th className="py-1 pr-3 text-right font-semibold">Not known</th>
              <th className="py-1 pr-3 text-right font-semibold">To a person</th>
              <th className="py-1 pr-3 text-right font-semibold">Blocked</th>
              <th className="py-1 text-right font-semibold">First sentence p95</th>
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
                <td className="py-1.5 text-right">
                  {r.latency_p95_ms == null ? "—" : `${(r.latency_p95_ms / 1000).toFixed(1)} s`}
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

function Tile({ label, value, hint }: { label: string; value: string | number; hint?: string }) {
  return (
    <div className="card p-4">
      <p className="text-xs font-semibold uppercase tracking-wide text-muted">{label}</p>
      <p className="mt-1 text-3xl font-extrabold text-accent">{value}</p>
      {hint && <p className="mt-1 text-xs text-muted">{hint}</p>}
    </div>
  );
}

function Bars({ title, rows }: { title: string; rows: [string, number][] }) {
  const max = Math.max(1, ...rows.map(([, n]) => n));
  return (
    <figure className="card p-5">
      <figcaption className="mb-3 font-bold">{title}</figcaption>
      {rows.length === 0 ? (
        <p className="text-sm text-muted">No data yet.</p>
      ) : (
        <ul className="space-y-2">
          {rows.map(([label, n]) => (
            <li key={label} className="grid grid-cols-[8rem_1fr_2.5rem] items-center gap-3 text-sm">
              <span className="truncate capitalize">{label.replace(/_/g, " ")}</span>
              <span className="h-3 rounded-full bg-subtle" aria-hidden>
                <span
                  className="brand-gradient block h-3 rounded-full"
                  style={{ width: `${(n / max) * 100}%` }}
                />
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
  const reviewer = me.role === "college_admin" || me.role === "admission_officer";
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
            <section aria-labelledby="conv-h">
              <h2 id="conv-h" className="mb-3 text-sm font-bold uppercase tracking-wide text-muted">
                Conversations
              </h2>
              <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
                <Tile
                  label="Conversations"
                  value={a.conversations.total}
                  hint={`${a.conversations.qualifying} with 2+ messages · ${a.conversations.single_message} single`}
                />
                <Tile
                  label="Resolution rate"
                  value={pct(a.conversations.resolution_rate)}
                  hint="Answered without a person, no 👎 or repeat at the end"
                />
                <Tile
                  label="Handoff rate"
                  value={pct(a.conversations.handoff_rate)}
                  hint={`${a.conversations.handed_off} asked for a person`}
                />
                <Tile
                  label="Abandoned"
                  value={pct(a.conversations.abandonment_rate)}
                  hint="Left within 10 min of an unrated answer"
                />
                <Tile
                  label="CSAT"
                  value={pct(a.feedback.csat)}
                  hint={`${a.feedback.ratings} chat ratings, 4–5 stars counted`}
                />
                <Tile
                  label="Answer acceptance"
                  value={pct(a.feedback.helpful_rate)}
                  hint={`${a.feedback.helpful} 👍 · ${a.feedback.not_helpful} 👎`}
                />
                <Tile
                  label="Voice vs chat"
                  value={pct(a.conversations.voice_share)}
                  hint={`voice share${
                    a.conversations.whatsapp_share != null
                      ? ` · WhatsApp ${pct(a.conversations.whatsapp_share)}`
                      : ""
                  } · ${a.sessions.total} visitor sessions`}
                />
                {a.leads && (
                  <Tile
                    label="Lead capture"
                    value={pct(a.conversations.lead_capture_rate)}
                    hint="Qualifying conversations with a lead"
                  />
                )}
              </div>
            </section>
            <section aria-labelledby="ops-h">
              <h2 id="ops-h" className="mb-3 text-sm font-bold uppercase tracking-wide text-muted">
                Operations
              </h2>
              <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
                {a.handoffs && (
                  <Tile label="Open tasks" value={a.handoffs.open} hint={`${a.handoffs.overdue} overdue`} />
                )}
                {a.handoffs && (
                  <Tile
                    label="Answered on time"
                    value={pct(a.handoffs.sla_met_rate)}
                    hint={a.handoffs.median_hours != null ? `median ${a.handoffs.median_hours} h` : undefined}
                  />
                )}
                {a.leads && (
                  <Tile label="Leads" value={a.leads.total} hint={`${a.leads.via_guardian} via a parent`} />
                )}
                {a.leads && (
                  <Tile
                    label="Hot leads"
                    value={a.leads.hot}
                    hint={`${a.leads.warm} warm · ${a.leads.cold} cold`}
                  />
                )}
              </div>
            </section>
            {a.assistant === null && (
              <Notice>
                WhatsApp conversations and answer outcomes are missing: the assistant service did not respond.
                The other numbers cover the website only.
              </Notice>
            )}
            {a.assistant && <ByChannel rows={a.assistant.by_channel} />}
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
                <h2 id="quality-h" className="mb-3 text-sm font-bold uppercase tracking-wide text-muted">
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
