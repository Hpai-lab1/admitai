"use client";

import { useState } from "react";
import { Badge, Empty, Loading, Notice, PageHeader, SectionTitle, useApi } from "@/components/console/ui";
import { shortDate } from "@/lib/time";
import { filterQa, statusLabel as label, statusTone as tone, type Qa } from "@/components/console/knowledge";
import { Programmes } from "@/components/console/Programmes";

type Doc = {
  id: string;
  title: string;
  status: string;
  academic_year: string;
  owner: string;
  chunks: number;
};
type Correction = {
  id: string;
  corrected_answer: string;
  reason: string;
  status: string;
  author_name: string | null;
  created_at: string;
};

export default function Knowledge() {
  const docs = useApi<{ items: Doc[] }>("/kb/documents");
  const qa = useApi<{ items: Qa[] }>("/curated-qa");
  const corrections = useApi<Correction[]>("/corrections?limit=20");
  const [q, setQ] = useState("");
  const [status, setStatus] = useState<"live" | "other" | "all">("live");
  const answers = filterQa(qa.data?.items ?? [], q, status);
  const counts = {
    live: (qa.data?.items ?? []).filter((a) => a.status === "approved").length,
    other: (qa.data?.items ?? []).filter((a) => a.status !== "approved").length,
  };

  return (
    <>
      <PageHeader
        title="Knowledge"
        sub="What the assistant answers from: the programme catalogue, the college's documents and the answers staff have approved."
      />

      <Programmes />

      <section aria-labelledby="docs" className="mb-10">
        <SectionTitle id="docs" count={docs.data?.items.length}>
          Documents
        </SectionTitle>
        {docs.error && <Notice>{docs.error}</Notice>}
        {docs.loading && !docs.data ? (
          <Loading rows={2} />
        ) : !docs.data?.items.length ? (
          <Empty>No documents yet.</Empty>
        ) : (
          <div className="overflow-x-auto rounded-lg border border-line bg-surface">
            <table className="w-full text-left text-sm">
              <caption className="sr-only">Documents</caption>
              <thead className="border-b border-line bg-subtle/60 text-xs text-muted">
                <tr>
                  <th scope="col" className="px-4 py-2 font-medium">
                    Title
                  </th>
                  <th scope="col" className="px-4 py-2 font-medium">
                    Owner
                  </th>
                  <th scope="col" className="px-4 py-2 font-medium">
                    Year
                  </th>
                  <th scope="col" className="px-4 py-2 text-right font-medium">
                    Sections
                  </th>
                  <th scope="col" className="px-4 py-2 font-medium">
                    Status
                  </th>
                </tr>
              </thead>
              <tbody className="divide-y divide-line">
                {docs.data.items.map((d) => (
                  <tr key={d.id} className={d.status === "approved" ? "" : "text-muted"}>
                    <td className="px-4 py-2.5 font-medium">{d.title}</td>
                    <td className="px-4 py-2.5">{d.owner}</td>
                    <td className="whitespace-nowrap px-4 py-2.5">{d.academic_year}</td>
                    <td className="px-4 py-2.5 text-right tabular-nums">{d.chunks}</td>
                    <td className="px-4 py-2.5">
                      <Badge tone={tone(d.status)}>{label(d.status)}</Badge>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </section>

      <section aria-labelledby="qa" className="mb-10">
        <SectionTitle id="qa">Approved answers</SectionTitle>
        <div className="mb-3 flex flex-wrap items-center gap-3">
          <label htmlFor="qa-search" className="sr-only">
            Search answers
          </label>
          <input
            id="qa-search"
            type="search"
            className="field max-w-sm"
            placeholder="Search questions and answers"
            value={q}
            onChange={(e) => setQ(e.target.value)}
          />
          <div className="tabs border-b-0" role="tablist" aria-label="Answer status">
            {(
              [
                ["live", `In use (${counts.live})`],
                ["other", `Paused or expired (${counts.other})`],
                ["all", "All"],
              ] as const
            ).map(([v, l]) => (
              <button
                key={v}
                role="tab"
                aria-selected={status === v}
                className={`tab ${status === v ? "tab-on" : ""}`}
                onClick={() => setStatus(v)}
              >
                {l}
              </button>
            ))}
          </div>
        </div>
        {qa.error && <Notice>{qa.error}</Notice>}
        {qa.loading && !qa.data ? (
          <Loading rows={3} />
        ) : !answers.length ? (
          <Empty>{q ? "No answer matches that search." : "No answers here."}</Empty>
        ) : (
          <ul className="divide-y divide-line rounded-lg border border-line bg-surface">
            {answers.map((a) => (
              <li key={a.id} className="px-4 py-3">
                <div className="flex items-start gap-3">
                  <p
                    className={`min-w-0 flex-1 text-sm font-medium ${a.status === "approved" ? "" : "text-muted"}`}
                  >
                    {a.question}
                  </p>
                  <Badge tone={tone(a.status)}>{label(a.status)}</Badge>
                </div>
                <p className="mt-1 line-clamp-2 text-sm text-muted">{a.answer}</p>
              </li>
            ))}
          </ul>
        )}
      </section>

      <section aria-labelledby="corr">
        <SectionTitle id="corr">Recent corrections from staff</SectionTitle>
        {corrections.error && <Notice>{corrections.error}</Notice>}
        {!corrections.data?.length ? (
          <Empty>None yet. Corrections come from Review answers and from replies in the queue.</Empty>
        ) : (
          <ul className="divide-y divide-line rounded-lg border border-line bg-surface">
            {corrections.data.map((c) => (
              <li key={c.id} className="px-4 py-3 text-sm">
                <p className="text-xs text-muted">
                  {c.author_name ?? "Staff"} · {shortDate(c.created_at)} · {c.status}
                </p>
                <p className="mt-1 line-clamp-2">{c.corrected_answer}</p>
              </li>
            ))}
          </ul>
        )}
      </section>
    </>
  );
}
