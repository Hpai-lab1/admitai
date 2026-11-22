"use client";

import { useState } from "react";
import { Empty, Loading, Notice, SectionTitle, useApi } from "@/components/console/ui";

export type Fee = { category: string; component: string; amount: string; currency: string; period: string };
export type Program = {
  code: string;
  name: string;
  department: string | null;
  level: string;
  duration_months: number | null;
  intake: number | null;
  eligibility: string | null;
  contact: string | null;
  academic_year: string;
  fees: Fee[] | null;
};

const LEVELS: Record<string, string> = {
  UG: "Undergraduate",
  PG: "Postgraduate",
  PhD: "Ph.D.",
  Diploma: "Diploma / training",
};

/** The amount the way the college website writes it: ₹6,00,000 (Indian grouping), USD 3,500. */
export function money(amount: string, currency: string): string {
  const n = Number(amount);
  if (!Number.isFinite(n)) return `${amount} ${currency}`;
  return currency === "INR" ? `₹${n.toLocaleString("en-IN")}` : `${currency} ${n.toLocaleString("en-US")}`;
}

/** "4 years" when whole years, otherwise months; nothing is guessed when the website gives none. */
export function duration(months: number | null): string {
  if (!months) return "Not stated";
  return months % 12 === 0 ? `${months / 12} year${months === 12 ? "" : "s"}` : `${months} months`;
}

const cap = (s: string) => s.charAt(0).toUpperCase() + s.slice(1);

export function feeLabel(f: Fee): string {
  const cat = f.category === "general" ? "" : `${f.category === "nri" ? "NRI" : cap(f.category)} quota · `;
  const comp = f.component === "total" ? "Total fee" : cap(f.component);
  const per = !f.period || f.period === "not stated" ? "" : ` ${f.period}`;
  return `${cat}${comp}: ${money(f.amount, f.currency)}${per}`;
}

/** Read-only: these are the exact values the assistant quotes. A fee changes only through an
 * approved correction ("Correct this answer"), never here. */
export function Programmes() {
  const progs = useApi<{ items: Program[] }>("/catalogue/programs");
  const [q, setQ] = useState("");
  const items = (progs.data?.items ?? []).filter((p) =>
    `${p.name} ${p.code} ${p.department ?? ""}`.toLowerCase().includes(q.trim().toLowerCase()),
  );

  return (
    <section aria-labelledby="programmes" className="mb-10">
      <SectionTitle id="programmes" count={progs.data?.items.length}>
        Programmes and fees
      </SectionTitle>
      <p className="mb-3 text-sm text-muted">
        What the assistant quotes, exactly. Read-only: to change a fee, use “Correct this answer”; the change
        goes through approval and testing.
      </p>
      <label htmlFor="prog-search" className="sr-only">
        Search programmes
      </label>
      <input
        id="prog-search"
        type="search"
        className="field mb-3 max-w-sm"
        placeholder="Search programmes"
        value={q}
        onChange={(e) => setQ(e.target.value)}
      />
      {progs.error && <Notice>{progs.error}</Notice>}
      {progs.loading && !progs.data ? (
        <Loading rows={3} />
      ) : !items.length ? (
        <Empty>{q ? "No programme matches that search." : "No programmes in the catalogue yet."}</Empty>
      ) : (
        <div className="overflow-x-auto rounded-lg border border-line bg-surface">
          <table className="w-full text-left text-sm">
            <caption className="sr-only">Programmes and fees</caption>
            <thead className="border-b border-line bg-subtle/60 text-xs text-muted">
              <tr>
                <th scope="col" className="px-4 py-2 font-medium">
                  Programme
                </th>
                <th scope="col" className="px-4 py-2 font-medium">
                  Level
                </th>
                <th scope="col" className="px-4 py-2 text-right font-medium">
                  Seats
                </th>
                <th scope="col" className="px-4 py-2 font-medium">
                  Duration
                </th>
                <th scope="col" className="px-4 py-2 font-medium">
                  Fees ({progs.data?.items[0]?.academic_year})
                </th>
              </tr>
            </thead>
            <tbody className="divide-y divide-line">
              {items.map((p) => (
                <tr key={p.code} className="align-top">
                  <th scope="row" className="px-4 py-2.5 font-medium">
                    {p.name}
                    <span className="block text-xs font-normal text-muted">
                      {p.code}
                      {p.department ? ` · ${p.department}` : ""}
                    </span>
                    {p.eligibility && (
                      <details className="mt-1 text-xs font-normal text-muted">
                        <summary className="cursor-pointer">Eligibility</summary>
                        <p className="mt-1 whitespace-pre-line">{p.eligibility.replaceAll("; ", "\n")}</p>
                      </details>
                    )}
                  </th>
                  <td className="whitespace-nowrap px-4 py-2.5">{LEVELS[p.level] ?? p.level}</td>
                  <td className="px-4 py-2.5 text-right tabular-nums">{p.intake ?? "—"}</td>
                  <td className={`whitespace-nowrap px-4 py-2.5 ${p.duration_months ? "" : "text-muted"}`}>
                    {duration(p.duration_months)}
                  </td>
                  <td className="px-4 py-2.5">
                    {p.fees?.length ? (
                      <ul className="space-y-0.5">
                        {p.fees.map((f) => (
                          <li key={`${f.category}-${f.component}-${f.amount}`} className="tabular-nums">
                            {feeLabel(f)}
                          </li>
                        ))}
                      </ul>
                    ) : (
                      <span className="text-muted">Not stated</span>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </section>
  );
}
