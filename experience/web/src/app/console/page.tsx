"use client";

import Link from "next/link";
import { useEffect, useRef, useState } from "react";
import { staff } from "@/lib/api";
import { Badge, Empty, Loading, Notice, PageHeader, bandTone, useApi } from "@/components/console/ui";
import { useMe } from "@/components/console/Shell";
import { ADMISSION_DESK, isAnalyticsOnly, isDepartmentRole } from "@/lib/roles";
import { slaText } from "@/lib/time";
import { Today } from "@/components/console/today";
import { sortQueue } from "@/components/console/queue";
import type { Handoff } from "@/lib/types";

const TABS = [
  ["active", "Open"],
  ["resolved", "Resolved"],
] as const;

export default function Queue() {
  const me = useMe();
  const [tab, setTab] = useState<"active" | "resolved">("active");
  // Console pages mount only in the browser (Shell waits for the user), so the URL is there.
  // "Needs you today" links to ?mine=true for the tasks you claimed.
  const [mine, setMine] = useState(() => new URLSearchParams(window.location.search).get("mine") === "true");
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

  if (isAnalyticsOnly(me.role))
    return (
      <>
        <Today />
        <Notice kind="info">Your role doesn&apos;t work the queue.</Notice>
      </>
    );

  return (
    <>
      <Today />
      <PageHeader
        title="Callbacks and handoffs"
        sub={
          isDepartmentRole(me.role)
            ? `Questions for ${me.departments.join(", ")}`
            : ADMISSION_DESK.includes(me.role)
              ? "Admissions queue"
              : "All queues"
        }
      >
        <button className="btn-quiet" onClick={() => void reload()}>
          Refresh
        </button>
      </PageHeader>

      <div className="mb-4 flex flex-wrap items-center gap-2">
        {/* Only the tabs are in the tab list; the checkbox beside it is not a tab (WCAG 4.1.2). */}
        <div className="tabs" role="tablist" aria-label="Status">
          {TABS.map(([v, l]) => (
            <button
              key={v}
              role="tab"
              aria-selected={tab === v}
              onClick={() => setTab(v)}
              className={`tab ${tab === v ? "tab-on" : ""}`}
            >
              {l}
            </button>
          ))}
        </div>
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

      <p className="mb-3 hidden text-xs text-muted lg:block">
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
        <Empty>
          {tab === "active"
            ? mine
              ? "Nothing claimed by you is open."
              : "Nothing is waiting."
            : "No resolved tasks yet."}
        </Empty>
      ) : (
        <div className="overflow-hidden rounded-lg border border-line bg-surface">
          {/* Column labels for sighted users; each row also says what it is to a screen reader. */}
          <div
            aria-hidden
            className="hidden grid-cols-[9rem_minmax(0,1fr)_12rem_9rem_8rem] gap-4 border-b border-line bg-subtle/60 px-4 py-2 text-xs font-medium text-muted lg:grid"
          >
            <span>{tab === "active" ? "Due" : "Resolved task"}</span>
            <span>Question</span>
            <span>Person</span>
            <span>Queue</span>
            <span>Owner</span>
          </div>
          <ul className="divide-y divide-line">
            {sortQueue(data, tab).map((h, idx) => {
              const sla = slaText(h.sla_due_at);
              const open = h.status !== "resolved";
              return (
                <li key={h.id}>
                  <Link
                    ref={(el) => {
                      links.current[idx] = el;
                    }}
                    data-id={h.id}
                    href={`/console/handoffs/${h.id}`}
                    className="grid gap-1 px-4 py-3 hover:bg-subtle/60 focus-visible:bg-subtle focus-visible:outline-none lg:grid-cols-[9rem_minmax(0,1fr)_12rem_9rem_8rem] lg:items-center lg:gap-4"
                  >
                    <span className="text-sm">
                      {open ? (
                        <span className={`font-semibold ${sla.overdue ? "text-danger" : "text-ink"}`}>
                          {sla.overdue
                            ? `Overdue ${sla.text.replace("overdue by ", "")}`
                            : sla.text.replace("due in ", "In ")}
                        </span>
                      ) : (
                        <span className="text-muted">Resolved</span>
                      )}
                    </span>
                    <span className="min-w-0">
                      <span className="block truncate text-sm font-medium text-ink">{h.reason}</span>
                      <span className="text-xs text-muted">
                        {h.kind === "callback" ? "Callback" : "Handoff"}
                      </span>
                    </span>
                    <span className="min-w-0 text-sm">
                      <span className="sr-only">Person: </span>
                      <span className="flex items-center gap-2">
                        <span className="truncate">{h.lead?.name ?? "Visitor"}</span>
                        {h.lead && <Badge tone={bandTone(h.lead.band)}>{h.lead.band}</Badge>}
                      </span>
                      <span className="block truncate text-xs text-muted">
                        {h.lead?.is_minor ? "Under 18: contact the guardian" : ""}
                        {!h.lead?.is_minor && h.preferred_channel ? `Prefers ${h.preferred_channel}` : ""}
                        {h.preferred_time ? ` · ${h.preferred_time}` : ""}
                      </span>
                    </span>
                    <span className="text-sm text-muted">
                      <span className="sr-only">Queue: </span>
                      {h.department ?? "Admissions"}
                    </span>
                    <span className="truncate text-sm">
                      <span className="sr-only">Owner: </span>
                      {h.status === "claimed" ? (
                        (h.assigned_name ?? "Someone")
                      ) : open ? (
                        <span className="text-muted">Unclaimed</span>
                      ) : (
                        <span className="text-muted">{h.assigned_name ?? "—"}</span>
                      )}
                    </span>
                  </Link>
                </li>
              );
            })}
          </ul>
        </div>
      )}
    </>
  );
}
