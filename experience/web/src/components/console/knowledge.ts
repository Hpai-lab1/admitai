export type Qa = { id: string; question: string; answer: string; status: string };

/** What each status means for visitors: in use (green), needs a person (amber), not used (grey/red). */
export const STATUS_TONE: Record<string, "green" | "amber" | "red" | "gray"> = {
  approved: "green",
  processing: "amber",
  draft: "amber",
  needs_review: "amber",
  suspended: "amber",
  expired: "gray",
  rejected: "red",
};
const STATUS_LABEL: Record<string, string> = {
  approved: "In use",
  processing: "Reading",
  draft: "Draft",
  needs_review: "Needs review",
  suspended: "Paused",
  expired: "Expired",
  rejected: "Rejected",
};
export const statusLabel = (s: string) => STATUS_LABEL[s] ?? s.replace("_", " ");
export const statusTone = (s: string) => STATUS_TONE[s] ?? "gray";

/** Curated answers matching the search and the status filter ("live" = in use). */
export function filterQa(items: Qa[], q: string, status: string): Qa[] {
  const words = q.toLowerCase().split(/\s+/).filter(Boolean);
  return items.filter(
    (a) =>
      (status === "all" || (status === "live" ? a.status === "approved" : a.status !== "approved")) &&
      words.every((w) => `${a.question} ${a.answer}`.toLowerCase().includes(w)),
  );
}
