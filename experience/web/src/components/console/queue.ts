import type { Handoff } from "@/lib/types";

/** Open tasks: overdue first, then the soonest due (what to do next is at the top). Resolved: newest. */
export function sortQueue(items: Handoff[], tab: "active" | "resolved"): Handoff[] {
  const due = (h: Handoff) => new Date(h.sla_due_at).getTime();
  return [...items].sort((a, b) =>
    tab === "resolved"
      ? new Date(b.created_at).getTime() - new Date(a.created_at).getTime()
      : due(a) - due(b),
  );
}
