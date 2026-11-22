"use client";

import { useCallback, useEffect, useState } from "react";
import { ApiError, staff } from "@/lib/api";

export function useApi<T>(path: string | null) {
  const [data, setData] = useState<T | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);

  const fetchInto = useCallback(
    (p: string) =>
      staff<T>(p)
        .then((d) => {
          setData(d);
          setError(null);
        })
        .catch((e: unknown) => setError(e instanceof ApiError ? e.message : "Something went wrong"))
        .finally(() => setLoading(false)),
    [],
  );

  useEffect(() => {
    if (path) void fetchInto(path);
  }, [path, fetchInto]);

  const reload = useCallback(async () => {
    if (!path) return;
    setLoading(true);
    await fetchInto(path);
  }, [path, fetchInto]);

  return { data, error, loading, reload, setData };
}

/** Reload every `ms` while something is in progress (a change being tested), so a badge
 *  moves on to "Live" or "Failed" without the page being reloaded by hand. */
export function useRefreshWhile(active: boolean, reload: () => Promise<void>, ms = 20000) {
  useEffect(() => {
    if (!active) return;
    const t = setInterval(() => void reload(), ms);
    return () => clearInterval(t);
  }, [active, reload, ms]);
}

export function PageHeader({
  title,
  sub,
  children,
}: {
  title: string;
  sub?: string;
  children?: React.ReactNode;
}) {
  return (
    <div className="mb-6 flex flex-wrap items-end gap-3 border-b border-line pb-4">
      <div className="mr-auto min-w-0">
        <h1 className="text-xl font-semibold tracking-tight text-ink">{title}</h1>
        {sub && <p className="mt-1 max-w-3xl text-sm text-muted">{sub}</p>}
      </div>
      {children && <div className="flex flex-wrap items-center gap-2">{children}</div>}
    </div>
  );
}

const TONES = {
  gray: "bg-subtle text-ink",
  red: "bg-red-100 text-red-800",
  green: "bg-green-100 text-green-800",
  amber: "bg-amber-100 text-amber-900",
  // The brand colour is only guaranteed 4.5:1 on the plain surface (theme.accessible), not on a
  // tint of itself, so the badge keeps the surface and shows the brand in its border and text.
  brand: "border border-brand/40 bg-surface text-brand",
  purple: "bg-accent/10 text-accent",
} as const;

export function Badge({ tone = "gray", children }: { tone?: keyof typeof TONES; children: React.ReactNode }) {
  return (
    <span className={`inline-flex rounded-full px-2 py-0.5 text-xs font-semibold ${TONES[tone]}`}>
      {children}
    </span>
  );
}

export const bandTone = (b: string) =>
  (b === "hot" ? "red" : b === "warm" ? "amber" : "gray") as keyof typeof TONES;

export function Notice({
  kind = "error",
  children,
}: {
  kind?: "error" | "ok" | "info";
  children: React.ReactNode;
}) {
  const cls =
    kind === "error"
      ? "bg-red-50 text-red-800"
      : kind === "ok"
        ? "bg-green-50 text-green-900"
        : "bg-blue-50 text-blue-900";
  return (
    <p role={kind === "error" ? "alert" : "status"} className={`mb-4 rounded-lg px-4 py-3 text-sm ${cls}`}>
      {children}
    </p>
  );
}

/** Skeleton rows while a list loads (PRD 13.4: skeletons, not spinners, for lists). */
export function Loading({ rows = 3 }: { rows?: number }) {
  return (
    <div role="status" className="space-y-3">
      <span className="sr-only">Loading…</span>
      {Array.from({ length: rows }, (_, i) => (
        <div key={i} className="card space-y-2 p-4" aria-hidden>
          <div className="skeleton h-4 w-1/3" />
          <div className="skeleton h-4 w-3/4" />
        </div>
      ))}
    </div>
  );
}

export function Empty({ children }: { children: React.ReactNode }) {
  return (
    <p className="rounded-lg border border-dashed border-line px-6 py-10 text-center text-sm text-muted">
      {children}
    </p>
  );
}

/** A section heading inside a page: one size everywhere, with an optional count and actions. */
export function SectionTitle({
  id,
  children,
  count,
  actions,
}: {
  id?: string;
  children: React.ReactNode;
  count?: number;
  actions?: React.ReactNode;
}) {
  return (
    <div className="mb-3 flex flex-wrap items-center gap-2">
      <h2 id={id} className="text-base font-semibold text-ink">
        {children}
        {count != null && <span className="ml-2 text-sm font-normal text-muted">{count}</span>}
      </h2>
      {actions && <div className="ml-auto flex items-center gap-2">{actions}</div>}
    </div>
  );
}
