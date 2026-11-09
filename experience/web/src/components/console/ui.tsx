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
    <div className="mb-6 flex flex-wrap items-end gap-3">
      <div className="mr-auto">
        <h1 className="text-2xl font-extrabold text-accent">{title}</h1>
        {sub && <p className="mt-1 text-sm text-muted">{sub}</p>}
      </div>
      {children}
    </div>
  );
}

const TONES = {
  gray: "bg-subtle text-ink",
  red: "bg-red-100 text-red-800",
  green: "bg-green-100 text-green-800",
  amber: "bg-amber-100 text-amber-900",
  brand: "bg-brand/10 text-brand",
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
  return <p className="card px-6 py-10 text-center text-sm text-muted">{children}</p>;
}
