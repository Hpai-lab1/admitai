"use client";

import { useEffect, useRef, useState } from "react";
import { Badge, Loading, Notice, PageHeader, useApi } from "@/components/console/ui";
import { useMe } from "@/components/console/Shell";
import { Webhooks } from "@/components/console/Webhooks";
import { ApiError, staff } from "@/lib/api";
import { ROLE_LABEL, type Role } from "@/lib/roles";
import { contrast } from "@/lib/theme";
import { shortDate } from "@/lib/time";

type User = {
  id: string;
  email: string;
  full_name: string;
  role: Role;
  departments: string[];
  is_active: boolean;
  mfa_enabled: boolean;
  created_at: string;
};
type TenantConfig = {
  assistant_name: string;
  greeting: string;
  primary_color: string;
  secondary_color: string;
  accent_color: string;
  font: "system" | "montserrat" | "inter" | "noto-sans";
  logo_url: string | null;
  starter_questions: string[];
  topics: string[];
  departments: string[];
  response_time_hours: number;
  office_hours: string;
  consent_text_version: string;
  privacy_notice_url: string | null;
  apply_url: string | null;
  announcement: string | null;
};
type AuditRow = {
  id: number;
  at: string;
  actor_kind: string;
  actor: string | null;
  action: string;
  entity: string;
  entity_id: string | null;
};

const TABS = ["Staff", "College settings", "Integrations", "Audit log"] as const;
const ROLES: Role[] = ["college_admin", "admission_officer", "faculty", "marketing_manager"];

export default function Admin() {
  const [tab, setTab] = useState<(typeof TABS)[number]>("Staff");
  return (
    <>
      <PageHeader title="Admin" sub="Accounts, college settings and the audit trail." />
      <div role="tablist" aria-label="Admin sections" className="mb-6 flex gap-2">
        {TABS.map((t) => (
          <button
            key={t}
            role="tab"
            aria-selected={tab === t}
            onClick={() => setTab(t)}
            className={`rounded-full px-4 py-1.5 text-sm font-semibold ${tab === t ? "bg-accent text-white" : "bg-surface text-accent"}`}
          >
            {t}
          </button>
        ))}
      </div>
      {tab === "Staff" && <Staff />}
      {tab === "College settings" && <Settings />}
      {tab === "Integrations" && <Webhooks />}
      {tab === "Audit log" && <Audit />}
    </>
  );
}

function Staff() {
  const me = useMe();
  const { data, error, reload } = useApi<User[]>("/admin/users");
  const cfg = useApi<TenantConfig>("/admin/tenant");
  const [form, setForm] = useState({
    email: "",
    full_name: "",
    role: "admission_officer" as Role,
    department: "",
  });
  const [created, setCreated] = useState<{ email: string; password: string } | null>(null);
  const [msg, setMsg] = useState<string | null>(null);
  const departments = cfg.data?.departments ?? [];

  async function call(fn: () => Promise<unknown>) {
    setMsg(null);
    try {
      await fn();
      await reload();
    } catch (e) {
      setMsg(e instanceof ApiError ? e.message : "That didn't work");
    }
  }

  const create = (e: React.FormEvent) => {
    e.preventDefault();
    void call(async () => {
      const r = await staff<User & { temporary_password: string }>("/admin/users", {
        method: "POST",
        body: JSON.stringify({
          email: form.email,
          full_name: form.full_name,
          role: form.role,
          departments: form.role === "faculty" && form.department ? [form.department] : [],
        }),
      });
      setCreated({ email: r.email, password: r.temporary_password });
      setForm({ email: "", full_name: "", role: "admission_officer", department: "" });
    });
  };

  const patch = (u: User, body: object) =>
    call(() => staff(`/admin/users/${u.id}`, { method: "PATCH", body: JSON.stringify(body) }));

  return (
    <div className="grid gap-6 lg:grid-cols-3">
      <section className="lg:col-span-2">
        {(error || msg) && <Notice>{error ?? msg}</Notice>}
        {!data ? (
          <Loading />
        ) : (
          <div className="card overflow-x-auto">
            <table className="w-full text-left text-sm">
              <caption className="sr-only">Staff accounts</caption>
              <thead className="bg-paper text-xs uppercase tracking-wide text-muted">
                <tr>
                  <th className="px-4 py-3">Name</th>
                  <th className="px-4 py-3">Role</th>
                  <th className="px-4 py-3">Status</th>
                  <th className="px-4 py-3">
                    <span className="sr-only">Actions</span>
                  </th>
                </tr>
              </thead>
              <tbody className="divide-y divide-line">
                {data.map((u) => (
                  <tr key={u.id} className={u.is_active ? "" : "opacity-60"}>
                    <td className="px-4 py-3">
                      <p className="font-semibold">{u.full_name}</p>
                      <p className="text-xs text-muted">{u.email}</p>
                    </td>
                    <td className="px-4 py-3">
                      {ROLE_LABEL[u.role]}
                      {u.departments.length > 0 && (
                        <p className="text-xs text-muted">{u.departments.join(", ")}</p>
                      )}
                    </td>
                    <td className="space-x-1 px-4 py-3">
                      <Badge tone={u.is_active ? "green" : "gray"}>
                        {u.is_active ? "active" : "switched off"}
                      </Badge>
                      {u.mfa_enabled && <Badge tone="purple">2-step</Badge>}
                    </td>
                    <td className="whitespace-nowrap px-4 py-3 text-right">
                      {u.id !== me.id && (
                        <>
                          <button
                            className="btn-ghost px-2 py-1 text-xs"
                            onClick={() => void patch(u, { is_active: !u.is_active })}
                          >
                            {u.is_active ? "Switch off" : "Switch on"}
                          </button>
                          {u.mfa_enabled && (
                            <button
                              className="btn-ghost px-2 py-1 text-xs"
                              onClick={() => void patch(u, { reset_mfa: true })}
                            >
                              Reset 2-step
                            </button>
                          )}
                        </>
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </section>

      <section className="card h-fit p-5">
        <h2 className="mb-3 font-bold">Add a staff member</h2>
        {created && (
          <Notice kind="ok">
            Account created for {created.email}. Temporary password (shown once):{" "}
            <code className="select-all font-mono font-bold">{created.password}</code>
          </Notice>
        )}
        <form className="space-y-3" onSubmit={create}>
          <div>
            <label htmlFor="u-name" className="label">
              Full name
            </label>
            <input
              id="u-name"
              className="field"
              required
              value={form.full_name}
              onChange={(e) => setForm({ ...form, full_name: e.target.value })}
            />
          </div>
          <div>
            <label htmlFor="u-email" className="label">
              E-mail
            </label>
            <input
              id="u-email"
              type="email"
              className="field"
              required
              value={form.email}
              onChange={(e) => setForm({ ...form, email: e.target.value })}
            />
          </div>
          <div>
            <label htmlFor="u-role" className="label">
              Role
            </label>
            <select
              id="u-role"
              className="field"
              value={form.role}
              onChange={(e) => setForm({ ...form, role: e.target.value as Role })}
            >
              {ROLES.map((r) => (
                <option key={r} value={r}>
                  {ROLE_LABEL[r]}
                </option>
              ))}
            </select>
          </div>
          {form.role === "faculty" && (
            <div>
              <label htmlFor="u-dept" className="label">
                Department
              </label>
              <select
                id="u-dept"
                className="field"
                required
                value={form.department}
                onChange={(e) => setForm({ ...form, department: e.target.value })}
              >
                <option value="">Choose…</option>
                {departments.map((d) => (
                  <option key={d}>{d}</option>
                ))}
              </select>
            </div>
          )}
          <button className="btn-primary w-full">Create account</button>
        </form>
      </section>
    </div>
  );
}

function Settings() {
  const { data, error } = useApi<TenantConfig>("/admin/tenant");
  if (error) return <Notice>{error}</Notice>;
  if (!data) return <Loading />;
  return <SettingsForm initial={data} />;
}

function SettingsForm({ initial }: { initial: TenantConfig }) {
  const me = useMe();
  const [cfg, setCfg] = useState<TenantConfig>(initial);
  const [msg, setMsg] = useState<{ kind: "ok" | "error"; text: string } | null>(null);
  const frame = useRef<HTMLIFrameElement>(null);

  // live preview: send unsaved settings to the widget iframe
  useEffect(() => {
    frame.current?.contentWindow?.postMessage(
      { type: "admitai:preview", config: cfg },
      window.location.origin,
    );
  }, [cfg]);

  const set = <K extends keyof TenantConfig>(k: K, v: TenantConfig[K]) => setCfg({ ...cfg, [k]: v });
  const lines = (v: string) =>
    v
      .split("\n")
      .map((s) => s.trim())
      .filter(Boolean);
  const weak = contrast(cfg.primary_color, "#ffffff") < 4.5;

  async function save(e: React.FormEvent) {
    e.preventDefault();
    setMsg(null);
    try {
      setCfg(await staff<TenantConfig>("/admin/tenant", { method: "PUT", body: JSON.stringify(cfg) }));
      setMsg({ kind: "ok", text: "Saved. Visitors see the new settings on their next visit." });
    } catch (err) {
      setMsg({ kind: "error", text: err instanceof ApiError ? err.message : "Save failed" });
    }
  }

  const text = (k: keyof TenantConfig, label: string, hint?: string) => (
    <div>
      <label htmlFor={`c-${k}`} className="label">
        {label}
      </label>
      <input
        id={`c-${k}`}
        className="field"
        value={(cfg[k] as string | null) ?? ""}
        onChange={(e) => set(k, (e.target.value || null) as never)}
      />
      {hint && <p className="mt-1 text-xs text-muted">{hint}</p>}
    </div>
  );

  return (
    <div className="grid gap-6 lg:grid-cols-[1fr_400px]">
      <form className="card space-y-4 p-5" onSubmit={save}>
        {msg && <Notice kind={msg.kind}>{msg.text}</Notice>}
        <h2 className="font-bold">Look and feel</h2>
        <div className="grid gap-3 sm:grid-cols-3">
          {(
            [
              ["primary_color", "Main colour"],
              ["secondary_color", "Second colour"],
              ["accent_color", "Dark accent"],
            ] as const
          ).map(([k, l]) => (
            <div key={k}>
              <label htmlFor={`c-${k}`} className="label">
                {l}
              </label>
              <div className="flex items-center gap-2">
                <input
                  id={`c-${k}`}
                  type="color"
                  className="h-9 w-12 rounded border border-line"
                  value={cfg[k]}
                  onChange={(e) => set(k, e.target.value.toUpperCase())}
                />
                <code className="text-xs">{cfg[k]}</code>
              </div>
            </div>
          ))}
        </div>
        {weak && (
          <Notice kind="info">
            This colour is light, so buttons and links use a slightly darker shade to stay readable
            (accessibility rule).
          </Notice>
        )}
        <div>
          <label htmlFor="c-font" className="label">
            Font
          </label>
          <select
            id="c-font"
            className="field"
            value={cfg.font}
            onChange={(e) => set("font", e.target.value as TenantConfig["font"])}
          >
            <option value="system">System default</option>
            <option value="montserrat">Montserrat</option>
            <option value="inter">Inter</option>
            <option value="noto-sans">Noto Sans</option>
          </select>
        </div>
        {text("logo_url", "Logo address (URL)")}

        <h2 className="pt-2 font-bold">Assistant</h2>
        {text("assistant_name", "Assistant name")}
        {text("greeting", "Greeting")}
        {text(
          "announcement",
          "Announcement strip",
          "Short line shown under the header, like the notice bar on your website.",
        )}
        <div>
          <label htmlFor="c-topics" className="label">
            Topic buttons (one per line)
          </label>
          <textarea
            id="c-topics"
            className="field min-h-28"
            value={cfg.topics.join("\n")}
            onChange={(e) => set("topics", lines(e.target.value))}
          />
        </div>
        {text("apply_url", "Apply now link")}
        {text("privacy_notice_url", "Privacy notice link")}

        <h2 className="pt-2 font-bold">Response promise</h2>
        <div className="grid gap-3 sm:grid-cols-2">
          <div>
            <label htmlFor="c-sla" className="label">
              Reply within (hours)
            </label>
            <input
              id="c-sla"
              type="number"
              min={1}
              max={168}
              className="field"
              value={cfg.response_time_hours}
              onChange={(e) => set("response_time_hours", Number(e.target.value))}
            />
          </div>
          {text("office_hours", "Office hours")}
        </div>
        <div>
          <label htmlFor="c-depts" className="label">
            Departments (one per line)
          </label>
          <textarea
            id="c-depts"
            className="field min-h-24"
            value={cfg.departments.join("\n")}
            onChange={(e) => set("departments", lines(e.target.value))}
          />
        </div>
        <button className="btn-primary">Save settings</button>
      </form>

      <div className="lg:sticky lg:top-6 lg:h-fit">
        <p className="mb-2 text-sm font-semibold">Live preview</p>
        <iframe
          ref={frame}
          title="Widget preview with unsaved settings"
          src={`/widget?key=${encodeURIComponent(me.tenant.public_key)}&utm_source=console_preview`}
          onLoad={() =>
            frame.current?.contentWindow?.postMessage(
              { type: "admitai:preview", config: cfg },
              window.location.origin,
            )
          }
          className="h-[620px] w-full rounded-2xl border border-line bg-surface shadow-xl"
        />
      </div>
    </div>
  );
}

function Audit() {
  const [action, setAction] = useState("");
  const { data, error, loading } = useApi<AuditRow[]>(
    `/audit?limit=200${action ? `&action=${encodeURIComponent(action)}` : ""}`,
  );
  return (
    <>
      <div className="mb-4 flex gap-2">
        <label htmlFor="a-filter" className="sr-only">
          Filter by action
        </label>
        <select
          id="a-filter"
          className="field w-auto"
          value={action}
          onChange={(e) => setAction(e.target.value)}
        >
          <option value="">All actions</option>
          {[
            "auth.",
            "user.",
            "lead.",
            "handoff.",
            "consent.",
            "correction.",
            "improvement.",
            "tenant.",
            "webhook.",
            "privacy.",
          ].map((a) => (
            <option key={a} value={a}>
              {a.replace(".", "")}
            </option>
          ))}
        </select>
      </div>
      {error && <Notice>{error}</Notice>}
      {loading && !data ? (
        <Loading />
      ) : (
        <div className="card overflow-x-auto">
          <table className="w-full text-left text-sm">
            <caption className="sr-only">Audit log</caption>
            <thead className="bg-paper text-xs uppercase tracking-wide text-muted">
              <tr>
                <th className="px-4 py-3">When</th>
                <th className="px-4 py-3">Who</th>
                <th className="px-4 py-3">Action</th>
                <th className="px-4 py-3">On</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-line">
              {data?.map((r) => (
                <tr key={r.id}>
                  <td className="whitespace-nowrap px-4 py-2 text-muted">{shortDate(r.at)}</td>
                  <td className="px-4 py-2">{r.actor ?? r.actor_kind}</td>
                  <td className="px-4 py-2 font-mono text-xs">{r.action}</td>
                  <td className="px-4 py-2 text-muted">{r.entity}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </>
  );
}
