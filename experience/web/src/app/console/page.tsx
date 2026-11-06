"use client";

import Link from "next/link";
import { useEffect, useRef, useState } from "react";
import { staff } from "@/lib/api";
import { Badge, Empty, Loading, Notice, PageHeader, bandTone, useApi } from "@/components/console/ui";
import { useMe } from "@/components/console/Shell";
import { slaText } from "@/lib/time";
import type { Handoff } from "@/lib/types";

const TABS = [
  ["active", "Open"],
  ["resolved", "Resolved"],
] as const;

export default function Queue() {
  const me = useMe();
  const [tab, setTab] = useState<"active" | "resolved">("active");
  const [mine, setMine] = useState(false);
  const { data, error, loading, reload } = useApi<Handoff[]>(
    `/handoffs?status=${tab}${mine ? "&mine=true" : ""}`,
  );

  const links = useRef<(HTMLAnchorElement | null)[]>([]);
  const [claimed, setClaimed] = useState<string | null>(null);

  // Keyboard shortcuts (PRD 13.4): j / k move, Enter opens, c claims the focused task.
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      const el = e.target as HTMLElement;
      if (e.metaKey || e.ctrlKey || e.altKey || el.closest("input, textarea, select")) return;
      const items = links.current.filter(Boolean) as HTMLAnchorElement[];
      if (!items.length) return;
      const i = items.indexOf(document.activeElement as HTMLAnchorElement);
      if (e.key === "j" || e.key === "k") {
        e.preventDefault();
        const next = e.key === "j" ? Math.min(i + 1, items.length - 1) : Math.max(i - 1, 0);
        items[i < 0 ? 0 : next]?.focus();
      } else if (e.key === "c" && i >= 0) {
        const id = items[i]?.dataset.id;
        if (id)
          void staff(`/handoffs/${id}/claim`, { method: "POST" })
            .then(() => {
              setClaimed(id);
              return reload();
            })
            .catch(() => {});
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [reload]);

  if (me.role === "marketing_manager")
    return <Notice kind="info">Marketing doesn&apos;t work the queue.</Notice>;

  return (
    <>
      <PageHeader
        title="Callbacks and handoffs"
        sub={
          me.role === "faculty"
            ? `Questions for ${me.departments.join(", ")}`
            : me.role === "admission_officer"
              ? "Admissions queue"
              : "All queues"
        }
      >
        <button className="btn-secondary" onClick={() => void reload()}>
          Refresh
        </button>
      </PageHeader>

      <div className="mb-4 flex flex-wrap items-center gap-2" role="tablist" aria-label="Status">
        {TABS.map(([v, l]) => (
          <button
            key={v}
            role="tab"
            aria-selected={tab === v}
            onClick={() => setTab(v)}
            className={`rounded-full px-4 py-1.5 text-sm font-semibold ${tab === v ? "bg-accent text-white" : "bg-surface text-accent"}`}
          >
            {l}
          </button>
        ))}
        <label className="ml-4 flex items-center gap-2 text-sm">
          <input
            type="checkbox"
            checked={mine}
            onChange={(e) => setMine(e.target.checked)}
            className="accent-[rgb(var(--brand))]"
          />
          Only mine
        </label>
      </div>

      <p className="mb-3 text-xs text-muted">
        Keyboard: <kbd className="rounded border border-line px-1">j</kbd>/
        <kbd className="rounded border border-line px-1">k</kbd> move ·{" "}
        <kbd className="rounded border border-line px-1">Enter</kbd> open ·{" "}
        <kbd className="rounded border border-line px-1">c</kbd> claim
      </p>
      {claimed && <Notice kind="ok">Task claimed.</Notice>}
      {error && <Notice>{error}</Notice>}
      {loading && !data ? (
        <Loading />
      ) : !data?.length ? (
        <Empty>{tab === "active" ? "Nothing waiting. Well done!" : "No resolved tasks yet."}</Empty>
      ) : (
        <ul className="space-y-3">
          {data.map((h, idx) => {
            const sla = slaText(h.sla_due_at);
            return (
              <li key={h.id}>
                <Link
                  ref={(el) => {
                    links.current[idx] = el;
                  }}
                  data-id={h.id}
                  href={`/console/handoffs/${h.id}`}
                  className="card block p-4 transition hover:border-brand2"
                >
                  <div className="flex flex-wrap items-center gap-2">
                    <Badge tone={h.kind === "callback" ? "brand" : "purple"}>
                      {h.kind === "callback" ? "Callback" : "Handoff"}
                    </Badge>
                    <Badge>{h.department ?? "Admissions"}</Badge>
                    {h.lead && <Badge tone={bandTone(h.lead.band)}>{h.lead.band}</Badge>}
                    {h.status !== "resolved" && (
                      <Badge tone={sla.overdue ? "red" : "green"}>{sla.text}</Badge>
                    )}
                    {h.status === "claimed" && (
                      <Badge tone="amber">With {h.assigned_name ?? "someone"}</Badge>
                    )}
                  </div>
                  <p className="mt-2 font-semibold">{h.reason}</p>
                  <p className="mt-1 text-sm text-muted">
                    {h.lead?.name ?? "Visitor"}
                    {h.lead?.is_minor ? " (under 18, contact the guardian)" : ""}
                    {h.preferred_channel ? ` · prefers ${h.preferred_channel}` : ""}
                    {h.preferred_time ? ` · ${h.preferred_time}` : ""}
                  </p>
                </Link>
              </li>
            );
          })}
        </ul>
      )}
    </>
  );
}
