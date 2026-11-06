"use client";

import { useState } from "react";
import { Badge, Empty, Loading, Notice, useApi } from "@/components/console/ui";
import { ApiError, staff } from "@/lib/api";
import { shortDate } from "@/lib/time";

// Outbound webhooks (PRD 12.2): the college's own systems (CRM, ERP) hear about leads, handoffs and
// knowledge changes. Only the College Admin sees this tab.

type Endpoint = {
  id: string;
  url: string;
  events: string[];
  active: boolean;
  created_at: string;
  last_7_days: Record<string, number>;
};
type Delivery = {
  id: number;
  type: string;
  status: "pending" | "delivered" | "failed" | "cancelled";
  attempts: number;
  last_status: number | null;
  last_error: string | null;
  created_at: string;
};

const TONE = { delivered: "green", pending: "amber", failed: "red", cancelled: "gray" } as const;

export function Webhooks() {
  const { data, error, loading, reload } = useApi<{ events: string[]; items: Endpoint[] }>("/webhooks");
  const [url, setUrl] = useState("");
  const [events, setEvents] = useState<string[]>(["lead.created", "lead.updated"]);
  const [secret, setSecret] = useState<{ url: string; secret: string } | null>(null);
  const [problem, setProblem] = useState<string | null>(null);
  const [open, setOpen] = useState<string | null>(null);

  async function call<T>(fn: () => Promise<T>): Promise<T | null> {
    setProblem(null);
    try {
      const out = await fn();
      await reload();
      return out;
    } catch (e) {
      setProblem(e instanceof ApiError ? e.message : "Something went wrong");
      return null;
    }
  }

  async function add(e: React.FormEvent) {
    e.preventDefault();
    const made = await call(() =>
      staff<Endpoint & { secret: string }>("/webhooks", {
        method: "POST",
        body: JSON.stringify({ url, events }),
      }),
    );
    if (made) {
      setSecret({ url: made.url, secret: made.secret });
      setUrl("");
    }
  }

  async function rotate(ep: Endpoint) {
    const made = await call(() =>
      staff<Endpoint & { secret: string }>(`/webhooks/${ep.id}/rotate`, { method: "POST" }),
    );
    if (made) setSecret({ url: made.url, secret: made.secret });
  }

  return (
    <div className="grid gap-6 lg:grid-cols-3">
      <section className="space-y-4 lg:col-span-2">
        {error && <Notice>{error}</Notice>}
        {problem && <Notice>{problem}</Notice>}
        {secret && (
          <div className="card border-2 border-amber-400 p-4" role="status">
            <p className="font-semibold">Signing secret for {secret.url}</p>
            <p className="mt-1 text-sm text-muted">
              Copy it now: it is not shown again. Your system checks each request&apos;s X-AdmitAI-Signature
              with it.
            </p>
            <code className="mt-2 block break-all rounded bg-subtle p-2 text-sm">{secret.secret}</code>
            <button className="btn-secondary mt-3" onClick={() => setSecret(null)}>
              I have saved it
            </button>
          </div>
        )}
        {loading && !data ? (
          <Loading />
        ) : !data?.items.length ? (
          <Empty>No webhooks yet. Add your CRM&apos;s address to send it new leads as they arrive.</Empty>
        ) : (
          data.items.map((ep) => (
            <article key={ep.id} className="card p-4">
              <div className="flex flex-wrap items-center gap-2">
                <p className="break-all font-semibold">{ep.url}</p>
                {!ep.active && <Badge tone="gray">stopped</Badge>}
              </div>
              <p className="mt-1 text-sm text-muted">{ep.events.join(", ")}</p>
              <p className="mt-1 text-sm">
                Last 7 days:{" "}
                {Object.keys(ep.last_7_days).length
                  ? Object.entries(ep.last_7_days)
                      .map(([s, n]) => `${n} ${s}`)
                      .join(" · ")
                  : "nothing sent"}
              </p>
              {ep.active && (
                <div className="mt-3 flex flex-wrap gap-2">
                  <button
                    className="btn-secondary"
                    onClick={() => void call(() => staff(`/webhooks/${ep.id}/test`, { method: "POST" }))}
                  >
                    Send a test
                  </button>
                  <button className="btn-secondary" onClick={() => setOpen(open === ep.id ? null : ep.id)}>
                    {open === ep.id ? "Hide deliveries" : "Deliveries"}
                  </button>
                  <button className="btn-secondary" onClick={() => void rotate(ep)}>
                    New secret
                  </button>
                  <button
                    className="btn-secondary"
                    onClick={() => void call(() => staff(`/webhooks/${ep.id}`, { method: "DELETE" }))}
                  >
                    Stop sending
                  </button>
                </div>
              )}
              {open === ep.id && <Deliveries id={ep.id} />}
            </article>
          ))
        )}
      </section>
      <form className="card h-fit space-y-3 p-5" onSubmit={(e) => void add(e)}>
        <h2 className="font-bold">Add a webhook</h2>
        <label className="block text-sm font-semibold" htmlFor="wh-url">
          Address (https)
        </label>
        <input
          id="wh-url"
          className="field"
          type="url"
          required
          placeholder="https://crm.example.edu/admitai"
          value={url}
          onChange={(e) => setUrl(e.target.value)}
        />
        <fieldset>
          <legend className="text-sm font-semibold">Events</legend>
          {(data?.events ?? []).map((ev) => (
            <label key={ev} className="mt-1 flex items-center gap-2 text-sm">
              <input
                type="checkbox"
                checked={events.includes(ev)}
                onChange={(e) =>
                  setEvents((cur) => (e.target.checked ? [...cur, ev] : cur.filter((x) => x !== ev)))
                }
              />
              {ev}
            </label>
          ))}
        </fieldset>
        <button className="btn-primary w-full" disabled={!url || !events.length}>
          Add
        </button>
        <p className="text-xs text-muted">
          Each request is signed, retried for 24 hours if your system is down, and carries an event id so
          repeats can be ignored.
        </p>
      </form>
    </div>
  );
}

function Deliveries({ id }: { id: string }) {
  const { data, error, loading } = useApi<{ items: Delivery[] }>(`/webhooks/${id}/deliveries`);
  if (error) return <Notice>{error}</Notice>;
  if (loading && !data) return <Loading rows={2} />;
  if (!data?.items.length) return <p className="mt-3 text-sm text-muted">Nothing sent yet.</p>;
  return (
    <table className="mt-3 w-full text-left text-sm">
      <thead className="text-xs uppercase tracking-wide text-muted">
        <tr>
          <th className="py-1 pr-3">Event</th>
          <th className="py-1 pr-3">Status</th>
          <th className="py-1 pr-3">Tries</th>
          <th className="py-1">When</th>
        </tr>
      </thead>
      <tbody>
        {data.items.map((d) => (
          <tr key={d.id} className="border-t border-subtle">
            <td className="py-1.5 pr-3">{d.type}</td>
            <td className="py-1.5 pr-3">
              <Badge tone={TONE[d.status]}>{d.status}</Badge>
              {d.last_error && <span className="ml-2 text-muted">{d.last_error}</span>}
            </td>
            <td className="py-1.5 pr-3 tabular-nums">{d.attempts}</td>
            <td className="py-1.5">{shortDate(d.created_at)}</td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}
