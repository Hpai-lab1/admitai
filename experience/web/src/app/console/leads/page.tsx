"use client";

import { Fragment, useState } from "react";
import { Badge, Empty, Loading, Notice, PageHeader, bandTone, useApi } from "@/components/console/ui";
import { useMe } from "@/components/console/Shell";
import { ApiError, staff, staffDownload } from "@/lib/api";
import { shortDate } from "@/lib/time";
import type { Lead } from "@/lib/types";

const STAGES = ["new", "contacted", "applied", "admitted", "lost"] as const;

export default function Leads() {
  const me = useMe();
  const [stage, setStage] = useState("");
  const [band, setBand] = useState("");
  const [q, setQ] = useState("");
  const [search, setSearch] = useState("");
  const [msg, setMsg] = useState<string | null>(null);
  const params = new URLSearchParams({ limit: "200" });
  if (stage) params.set("stage", stage);
  if (band) params.set("band", band);
  if (search) params.set("q", search);
  const { data, error, loading, setData } = useApi<{ items: Lead[]; total: number }>(`/leads?${params}`);
  const canEdit = ["college_admin", "admission_officer", "super_admin"].includes(me.role);

  const [notesFor, setNotesFor] = useState<string | null>(null);
  const [notes, setNotes] = useState("");

  async function patch(lead: Lead, body: { stage?: string; notes?: string }) {
    setMsg(null);
    try {
      const updated = await staff<Lead>(`/leads/${lead.id}`, {
        method: "PATCH",
        body: JSON.stringify(body),
      });
      setData((d) => d && { ...d, items: d.items.map((l) => (l.id === lead.id ? updated : l)) });
    } catch (e) {
      setMsg(e instanceof ApiError ? e.message : "Update failed");
    }
  }

  return (
    <>
      <PageHeader title="Leads" sub="People who agreed to be contacted. Hot leads first.">
        <button
          className="btn-secondary"
          onClick={() =>
            void staffDownload(`/leads/export?${params}`, "leads.csv").catch(() => setMsg("Export failed"))
          }
        >
          Export CSV
        </button>
      </PageHeader>
      <form
        className="mb-4 flex flex-wrap gap-2"
        onSubmit={(e) => {
          e.preventDefault();
          setSearch(q);
        }}
      >
        <label className="sr-only" htmlFor="q">
          Search
        </label>
        <input
          id="q"
          className="field max-w-xs"
          placeholder="Search name, city, programme"
          value={q}
          onChange={(e) => setQ(e.target.value)}
        />
        <label className="sr-only" htmlFor="stage">
          Stage
        </label>
        <select id="stage" className="field w-auto" value={stage} onChange={(e) => setStage(e.target.value)}>
          <option value="">All stages</option>
          {STAGES.map((s) => (
            <option key={s}>{s}</option>
          ))}
        </select>
        <label className="sr-only" htmlFor="band">
          Band
        </label>
        <select id="band" className="field w-auto" value={band} onChange={(e) => setBand(e.target.value)}>
          <option value="">All bands</option>
          <option>hot</option>
          <option>warm</option>
          <option>cold</option>
        </select>
        <button className="btn-primary">Search</button>
      </form>
      {(error || msg) && <Notice>{error ?? msg}</Notice>}
      {loading && !data ? (
        <Loading />
      ) : !data?.items.length ? (
        <Empty>No leads match.</Empty>
      ) : (
        <div className="card overflow-x-auto">
          <table className="w-full text-left text-sm">
            <caption className="sr-only">Leads ({data.total})</caption>
            <thead className="bg-paper text-xs uppercase tracking-wide text-muted">
              <tr>
                <th className="px-4 py-3">Name</th>
                <th className="px-4 py-3">Contact</th>
                <th className="px-4 py-3">Interested in</th>
                <th className="px-4 py-3">Score</th>
                <th className="px-4 py-3">Source</th>
                <th className="px-4 py-3">Stage</th>
                <th className="px-4 py-3">Created</th>
                <th className="px-4 py-3">
                  <span className="sr-only">Notes</span>
                </th>
              </tr>
            </thead>
            <tbody className="divide-y divide-line">
              {data.items.map((l) => (
                <Fragment key={l.id}>
                  <tr>
                    <td className="px-4 py-3 font-semibold">
                      {l.name ?? "—"}
                      {l.is_minor && <span className="ml-1 text-xs font-normal text-muted">(under 18)</span>}
                    </td>
                    <td className="px-4 py-3">
                      {l.is_minor
                        ? `Guardian: ${l.guardian_contact?.destination ?? "—"}`
                        : [l.phone, l.email].filter(Boolean).join(" · ") || "—"}
                    </td>
                    <td className="px-4 py-3">{l.program_interest.join(", ") || "—"}</td>
                    <td className="px-4 py-3">
                      <Badge tone={bandTone(l.band)}>
                        {l.band} · {l.score}
                      </Badge>
                    </td>
                    <td className="px-4 py-3">{l.source.utm?.utm_source ?? "direct"}</td>
                    <td className="px-4 py-3">
                      {canEdit ? (
                        <select
                          aria-label={`Stage for ${l.name ?? "lead"}`}
                          className="field w-auto py-1"
                          value={l.stage}
                          onChange={(e) => void patch(l, { stage: e.target.value })}
                        >
                          {STAGES.map((s) => (
                            <option key={s}>{s}</option>
                          ))}
                        </select>
                      ) : (
                        l.stage
                      )}
                    </td>
                    <td className="whitespace-nowrap px-4 py-3 text-muted">{shortDate(l.created_at)}</td>
                    <td className="px-4 py-3">
                      <button
                        className="btn-ghost px-2 text-xs"
                        aria-expanded={notesFor === l.id}
                        onClick={() => {
                          setNotesFor(notesFor === l.id ? null : l.id);
                          setNotes(l.notes ?? "");
                        }}
                      >
                        {l.notes ? "📝 Notes" : "Add note"}
                      </button>
                    </td>
                  </tr>
                  {notesFor === l.id && (
                    <tr>
                      <td colSpan={8} className="bg-paper px-4 py-3">
                        <form
                          className="flex flex-col gap-2 sm:flex-row"
                          onSubmit={(e) => {
                            e.preventDefault();
                            void patch(l, { notes }).then(() => setNotesFor(null));
                          }}
                        >
                          <label htmlFor={`notes-${l.id}`} className="sr-only">
                            Notes for {l.name ?? "this lead"}
                          </label>
                          <textarea
                            id={`notes-${l.id}`}
                            className="field min-h-20 flex-1"
                            maxLength={2000}
                            readOnly={!canEdit}
                            value={notes}
                            onChange={(e) => setNotes(e.target.value)}
                            placeholder="e.g. Called on 27 Oct, will apply after board results"
                          />
                          {canEdit && <button className="btn-primary self-start">Save note</button>}
                        </form>
                      </td>
                    </tr>
                  )}
                </Fragment>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </>
  );
}
