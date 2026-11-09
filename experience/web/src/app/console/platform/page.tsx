"use client";

import { useState } from "react";
import { Badge, Empty, Loading, Notice, PageHeader, useApi } from "@/components/console/ui";
import { ApiError, staff } from "@/lib/api";

type College = { id: string; slug: string; name: string; status: "onboarding" | "live" | "suspended" };
type Item = { key: string; label: string; ok: boolean; detail: string; blocking: boolean };
type Created = {
  id: string;
  slug: string;
  public_key: string;
  staff: { email: string; role: string; temporary_password: string }[];
  readiness: Item[];
};
type Launch = { status: string; reasons?: string[]; scores?: Record<string, number | null> };

const STATUS_TONE = { onboarding: "amber", live: "green", suspended: "gray" } as const;

/** PRD F10: a new college is configured, not coded. Upload its Tenant Pack, close the readiness
 *  checklist, preview it, then launch: it goes live only if its test set passes. */
export default function Platform() {
  const { data, error, loading, reload } = useApi<{ items: College[] }>("/platform/tenants");
  const [file, setFile] = useState<File | null>(null);
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState<{ kind: "ok" | "error"; text: string } | null>(null);
  const [created, setCreated] = useState<Created | null>(null);
  const [checks, setChecks] = useState<Record<string, Item[]>>({});
  const [launches, setLaunches] = useState<Record<string, Launch>>({});

  const fail = (e: unknown) => {
    const detail = e instanceof ApiError ? e.message : "That didn't work";
    setMsg({ kind: "error", text: detail });
  };

  async function upload(e: React.FormEvent) {
    e.preventDefault();
    if (!file) return;
    setBusy(true);
    setMsg(null);
    try {
      const form = new FormData();
      form.append("file", file);
      const r = await staff<Created>("/platform/tenants", { method: "POST", body: form });
      setCreated(r);
      setChecks((c) => ({ ...c, [r.id]: r.readiness }));
      setMsg({ kind: "ok", text: `${r.slug} created. It stays private until it passes its launch test.` });
      await reload();
    } catch (err) {
      fail(err);
    } finally {
      setBusy(false);
    }
  }

  async function check(id: string) {
    try {
      const r = await staff<{ items: Item[] }>(`/platform/tenants/${id}/readiness`);
      setChecks((c) => ({ ...c, [id]: r.items }));
    } catch (err) {
      fail(err);
    }
  }

  async function launch(id: string) {
    setMsg(null);
    try {
      await staff(`/platform/tenants/${id}/launch`, { method: "POST" });
      setLaunches((l) => ({ ...l, [id]: { status: "testing" } }));
      for (;;) {
        await new Promise((res) => setTimeout(res, 5000));
        const r = await staff<Launch>(`/platform/tenants/${id}/launch`);
        setLaunches((l) => ({ ...l, [id]: r }));
        if (r.status !== "testing") break;
      }
      await reload();
    } catch (err) {
      fail(err);
    }
  }

  return (
    <>
      <PageHeader
        title="Colleges"
        sub="Onboard a college from its Tenant Pack: no code changes. A new college is private until its launch test passes."
      />
      {msg && <Notice kind={msg.kind}>{msg.text}</Notice>}
      {error && <Notice>{error}</Notice>}

      <form className="card mb-6 flex flex-wrap items-end gap-3 p-4" onSubmit={(e) => void upload(e)}>
        <div className="min-w-0 flex-1">
          <label htmlFor="pack" className="text-sm font-medium">
            Tenant Pack (zip)
          </label>
          <input
            id="pack"
            type="file"
            accept=".zip,application/zip"
            className="field mt-1"
            onChange={(e) => setFile(e.target.files?.[0] ?? null)}
          />
        </div>
        <button className="btn-primary" disabled={!file || busy}>
          {busy ? "Loading the pack…" : "Create college"}
        </button>
      </form>

      {created && !!created.staff.length && (
        <div className="card mb-6 p-4 text-sm">
          <p className="font-semibold">
            Staff accounts for {created.slug}: share each password once, privately.
          </p>
          <p className="text-muted">They are not shown again; staff change them at first sign-in.</p>
          <ul className="mt-2 space-y-1 font-mono">
            {created.staff.map((s) => (
              <li key={s.email}>
                {s.email} ({s.role.replace("_", " ")}): {s.temporary_password}
              </li>
            ))}
          </ul>
        </div>
      )}

      {loading && !data ? (
        <Loading />
      ) : !data?.items.length ? (
        <Empty>No colleges yet.</Empty>
      ) : (
        <ul className="space-y-3">
          {data.items.map((c) => (
            <li key={c.id} className="card p-4">
              <div className="flex flex-wrap items-center gap-3">
                <p className="font-semibold">{c.name}</p>
                <span className="text-xs text-muted">{c.slug}</span>
                <Badge tone={STATUS_TONE[c.status]}>{c.status}</Badge>
                {c.status === "onboarding" && (
                  <span className="ml-auto flex gap-2">
                    <button className="btn-secondary px-3 text-xs" onClick={() => void check(c.id)}>
                      Readiness
                    </button>
                    <button
                      className="btn-primary px-3 text-xs"
                      disabled={launches[c.id]?.status === "testing"}
                      onClick={() => void launch(c.id)}
                    >
                      {launches[c.id]?.status === "testing" ? "Running the test set…" : "Launch"}
                    </button>
                  </span>
                )}
              </div>
              {checks[c.id] && (
                <ul className="mt-3 space-y-1 text-sm" aria-label={`Readiness of ${c.name}`}>
                  {checks[c.id]!.map((i) => (
                    <li key={i.key} className="flex gap-2">
                      <span aria-hidden>{i.ok ? "✅" : i.blocking ? "❌" : "⚠️"}</span>
                      <span>
                        {i.label}
                        {i.detail && <span className="text-muted"> · {i.detail}</span>}
                        <span className="sr-only">
                          {i.ok ? " (done)" : i.blocking ? " (blocks launch)" : " (advice)"}
                        </span>
                      </span>
                    </li>
                  ))}
                </ul>
              )}
              {launches[c.id] && launches[c.id]!.status !== "testing" && (
                <p className="mt-2 text-sm" aria-live="polite">
                  {launches[c.id]!.status === "live"
                    ? "Launched: the test set passed and the college is live."
                    : `Not launched: ${(launches[c.id]!.reasons ?? []).join("; ")}`}
                </p>
              )}
            </li>
          ))}
        </ul>
      )}
    </>
  );
}
