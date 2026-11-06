"use client";

import { Badge, Loading, Notice, PageHeader, useApi } from "@/components/console/ui";
import { shortDate } from "@/lib/time";

type Doc = {
  id: string;
  title: string;
  status: string;
  academic_year: string;
  owner: string;
  chunks: number;
};
type Qa = { id: string; question: string; answer: string; status: string };
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

  return (
    <>
      <PageHeader
        title="Knowledge"
        sub="What the assistant answers from. Documents and curated answers are managed by the knowledge service."
      />
      <section aria-labelledby="docs" className="mb-8">
        <h2 id="docs" className="mb-3 text-lg font-bold">
          Documents
        </h2>
        {docs.error && <Notice>{docs.error}</Notice>}
        {docs.loading && !docs.data ? (
          <Loading />
        ) : (
          <ul className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
            {docs.data?.items.map((d) => (
              <li key={d.id} className="card p-4">
                <div className="flex items-center gap-2">
                  <Badge tone={d.status === "indexed" ? "green" : "amber"}>{d.status}</Badge>
                  <span className="text-xs text-muted">{d.academic_year}</span>
                </div>
                <p className="mt-2 font-semibold">{d.title}</p>
                <p className="text-sm text-muted">
                  {d.owner} · {d.chunks} sections
                </p>
              </li>
            ))}
          </ul>
        )}
      </section>
      <section aria-labelledby="qa" className="mb-8">
        <h2 id="qa" className="mb-3 text-lg font-bold">
          Curated answers
        </h2>
        {qa.error && <Notice>{qa.error}</Notice>}
        <ul className="space-y-2">
          {qa.data?.items.map((q) => (
            <li key={q.id} className="card p-4">
              <p className="font-semibold">
                {q.question} <Badge tone={q.status === "published" ? "green" : "gray"}>{q.status}</Badge>
              </p>
              <p className="mt-1 text-sm text-muted">{q.answer}</p>
            </li>
          ))}
        </ul>
      </section>
      <section aria-labelledby="corr">
        <h2 id="corr" className="mb-3 text-lg font-bold">
          Recent corrections from staff
        </h2>
        {corrections.error && <Notice>{corrections.error}</Notice>}
        {!corrections.data?.length ? (
          <p className="text-sm text-muted">None yet. Corrections come from replies in the queue.</p>
        ) : (
          <ul className="space-y-2">
            {corrections.data.map((c) => (
              <li key={c.id} className="card p-4 text-sm">
                <p className="text-xs text-muted">
                  {c.author_name ?? "Staff"} · {shortDate(c.created_at)} · <Badge>{c.status}</Badge>
                </p>
                <p className="mt-1">{c.corrected_answer}</p>
              </li>
            ))}
          </ul>
        )}
      </section>
    </>
  );
}
