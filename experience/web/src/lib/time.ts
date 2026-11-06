/** "due in 3 h", "overdue by 2 h", "due in 25 min" for SLA badges. */
export function slaText(dueIso: string, now: Date = new Date()): { text: string; overdue: boolean } {
  const diff = new Date(dueIso).getTime() - now.getTime();
  const mins = Math.round(Math.abs(diff) / 60000);
  const span =
    mins < 60
      ? `${mins} min`
      : mins < 48 * 60
        ? `${Math.round(mins / 60)} h`
        : `${Math.round(mins / 1440)} days`;
  return diff >= 0
    ? { text: `due in ${span}`, overdue: false }
    : { text: `overdue by ${span}`, overdue: true };
}

export function shortDate(iso: string): string {
  return new Date(iso).toLocaleString("en-IN", {
    day: "numeric",
    month: "short",
    hour: "2-digit",
    minute: "2-digit",
  });
}
