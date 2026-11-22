"use client";

import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import { createContext, useContext, useEffect, useState } from "react";
import { ApiError, hasSession, signOut, staff, type Me } from "@/lib/api";
import { isAnalyticsOnly, navFor, ROLE_LABEL } from "@/lib/roles";
import { themeStyle } from "@/lib/theme";
import { countsByPage, type TodayItem } from "@/components/console/today";

const MeContext = createContext<Me | null>(null);

type Mode = "system" | "light" | "dark";
const MODE_KEY = "admitai.theme";

function applyMode(mode: Mode) {
  const root = document.documentElement;
  root.classList.remove("light", "dark");
  if (mode !== "system") root.classList.add(mode);
}

/** System / light / dark switch (PRD 13.5: manual toggle in the console). Remembered per browser. */
function ThemeToggle() {
  const [mode, setMode] = useState<Mode>("system");
  useEffect(() => {
    let saved: Mode = "system";
    try {
      saved = (localStorage.getItem(MODE_KEY) as Mode) || "system";
    } catch {
      /* storage blocked: follow the system */
    }
    applyMode(saved);
    // eslint-disable-next-line react-hooks/set-state-in-effect -- reading a stored preference once
    setMode(saved);
  }, []);
  const next: Record<Mode, Mode> = { system: "light", light: "dark", dark: "system" };
  const label = { system: "System", light: "Light", dark: "Dark" }[mode];
  return (
    <button
      className="btn-ghost min-h-9 px-2 text-xs font-medium text-muted"
      aria-label={`Colour theme: ${mode}. Switch to ${next[mode]}`}
      title={`Switch to ${next[mode]}`}
      onClick={() => {
        const m = next[mode];
        setMode(m);
        applyMode(m);
        try {
          localStorage.setItem(MODE_KEY, m);
        } catch {
          /* ignore */
        }
      }}
    >
      <span aria-hidden>Theme: {label}</span>
    </button>
  );
}

type BreakGlass = { id: string; reason: string; started_at: string; expires_at: string } | null;

/** Super-Admin only (PRD 5.4): personal data is hidden until a reason is given; access lasts 1 hour
 *  and the college admin is notified. */
function BreakGlassBar() {
  const [active, setActive] = useState<BreakGlass>(null);
  const [reason, setReason] = useState("");
  const [error, setError] = useState<string | null>(null);
  useEffect(() => {
    staff<{ active: BreakGlass }>("/break-glass")
      .then((r) => setActive(r.active))
      .catch(() => {});
  }, []);
  const start = async (e: React.FormEvent) => {
    e.preventDefault();
    setError(null);
    try {
      setActive(
        (
          await staff<{ active: BreakGlass }>("/break-glass", {
            method: "POST",
            body: JSON.stringify({ reason }),
          })
        ).active,
      );
      window.location.reload();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "That didn't work");
    }
  };
  const end = async () => {
    await staff("/break-glass/end", { method: "POST" }).catch(() => {});
    setActive(null);
    window.location.reload();
  };
  if (active) {
    return (
      <div
        role="status"
        className="flex flex-wrap items-center gap-3 bg-red-700 px-4 py-2 text-sm text-white md:px-8"
      >
        <strong>Break-glass access is on</strong>
        <span>
          until{" "}
          {new Date(active.expires_at).toLocaleTimeString("en-IN", { hour: "2-digit", minute: "2-digit" })} ·
          reason: {active.reason}
        </span>
        <button
          className="btn ml-auto border border-white px-3 text-white hover:bg-white/10"
          onClick={() => void end()}
        >
          End now
        </button>
      </div>
    );
  }
  return (
    <form
      onSubmit={start}
      className="flex flex-wrap items-center gap-2 bg-amber-100 px-4 py-2 text-sm text-amber-950 md:px-8"
    >
      <label htmlFor="bg-reason" className="font-semibold">
        Personal data is hidden. To see it, state a reason (1 hour, the college admin is told):
      </label>
      <input
        id="bg-reason"
        className="field max-w-sm flex-1"
        minLength={10}
        required
        placeholder="e.g. Ticket 42: parent says callback never came"
        value={reason}
        onChange={(e) => setReason(e.target.value)}
      />
      <button className="btn-primary">Start break-glass</button>
      {error && (
        <span role="alert" className="text-red-800">
          {error}
        </span>
      )}
    </form>
  );
}
export const useMe = () => useContext(MeContext) as Me;

type Notification = {
  id: number;
  kind: string;
  payload: {
    handoff_id?: string;
    reason?: string;
    // improvement.tested: an approved change finished its test
    proposal_id?: string;
    title?: string;
    passed?: boolean;
    taken_down?: boolean;
  };
  created_at: string;
  read: boolean;
};

/** An approved change finished its test: what the bell says. */
export function testedTitle(n: Pick<Notification, "payload">): string {
  const what = (n.payload.title ?? "Your change").replace(/^Expert correction: /, "");
  if (n.payload.passed) return `✅ Live now: ${what}`;
  if (n.payload.taken_down) return `❌ Failed the test and taken down: ${what}`;
  return `❌ Failed the test, nothing changed: ${what}`;
}

export function Shell({ children }: { children: React.ReactNode }) {
  const path = usePathname();
  const router = useRouter();
  const [me, setMe] = useState<Me | null>(null);
  const [notes, setNotes] = useState<{ unread: number; items: Notification[] }>({ unread: 0, items: [] });
  const [bell, setBell] = useState(false);
  // How much is waiting per section, shown beside the menu items (from GET /today).
  const [waiting, setWaiting] = useState<Record<string, { n: number; urgent: boolean }>>({});
  const isLogin = path === "/console/login";

  useEffect(() => {
    if (isLogin) return;
    if (!hasSession()) {
      router.replace("/console/login");
      return;
    }
    staff<Me>("/auth/me")
      .then(setMe)
      .catch(() => router.replace("/console/login"));
  }, [isLogin, router]);

  useEffect(() => {
    if (!me || isAnalyticsOnly(me.role)) return;
    const poll = () =>
      staff<{ unread: number; items: Notification[] }>("/notifications?limit=10")
        .then(setNotes)
        .catch(() => {});
    poll();
    const t = setInterval(poll, 30000);
    return () => clearInterval(t);
  }, [me]);

  useEffect(() => {
    if (!me) return;
    const load = () =>
      staff<{ items: TodayItem[] }>("/today")
        .then((d) => setWaiting(countsByPage(d.items)))
        .catch(() => {});
    load();
    const t = setInterval(load, 60000);
    return () => clearInterval(t);
  }, [me, path]);

  if (isLogin) return <>{children}</>;
  if (!me) {
    return (
      <p role="status" className="p-10 text-center text-sm text-muted">
        Loading…
      </p>
    );
  }

  const openNote = async (n: Notification) => {
    setBell(false);
    await staff(`/notifications/${n.id}/read`, { method: "POST" }).catch(() => {});
    setNotes((s) => ({
      unread: Math.max(0, s.unread - (n.read ? 0 : 1)),
      items: s.items.map((x) => (x.id === n.id ? { ...x, read: true } : x)),
    }));
    if (n.payload.handoff_id) router.push(`/console/handoffs/${n.payload.handoff_id}`);
    else if (n.kind === "improvement.tested") router.push("/console/approvals");
  };

  return (
    <MeContext.Provider value={me}>
      <div
        style={themeStyle(me.tenant.theme)}
        className="console flex min-h-screen bg-paper font-brand text-ink"
      >
        <a
          href="#content"
          className="sr-only-focusable absolute left-2 top-2 z-50 rounded bg-surface px-3 py-2"
        >
          Skip to content
        </a>
        <aside className="sticky top-0 hidden h-screen w-60 shrink-0 flex-col border-r border-line bg-surface md:flex">
          <div className="flex items-center gap-3 border-b border-line px-4 py-4">
            <span
              aria-hidden
              className="flex h-8 w-8 shrink-0 items-center justify-center rounded-md bg-brand text-sm font-bold"
              style={{ color: "var(--on-brand)" }}
            >
              {me.tenant.name.trim().charAt(0)}
            </span>
            <div className="min-w-0">
              <p className="line-clamp-2 text-sm font-semibold leading-tight" title={me.tenant.name}>
                {me.tenant.name}
              </p>
              <p className="text-xs text-muted">Staff console</p>
            </div>
          </div>
          <nav aria-label="Console" className="flex-1 overflow-y-auto px-3 py-3">
            <ul className="space-y-0.5">
              {navFor(me.role).map((n) => {
                const count = waiting[n.href];
                const active =
                  n.href === "/console"
                    ? path === "/console" || path.startsWith("/console/handoffs")
                    : path.startsWith(n.href);
                return (
                  <li key={n.href}>
                    <Link
                      href={n.href}
                      aria-current={active ? "page" : undefined}
                      className={`block rounded-md px-3 py-2 text-sm ${
                        active
                          ? "bg-subtle font-semibold text-ink shadow-[inset_3px_0_0_rgb(var(--brand))]"
                          : "font-medium text-muted hover:bg-subtle hover:text-ink"
                      }`}
                    >
                      <span className="flex items-center justify-between gap-2">
                        {n.label}
                        {count?.n ? (
                          <span
                            className={`min-w-5 rounded-full px-1.5 text-center text-xs font-semibold tabular-nums ${
                              count.urgent ? "bg-danger/15 text-danger" : "bg-subtle text-muted"
                            }`}
                          >
                            <span className="sr-only">, </span>
                            {count.n}
                            <span className="sr-only"> waiting</span>
                          </span>
                        ) : null}
                      </span>
                    </Link>
                  </li>
                );
              })}
            </ul>
          </nav>
          <div className="border-t border-line px-4 py-3 text-sm">
            <p className="truncate font-semibold">{me.full_name}</p>
            <p className="truncate text-xs text-muted">
              {ROLE_LABEL[me.role]}
              {me.departments.length ? ` · ${me.departments.join(", ")}` : ""}
            </p>
            <div className="mt-2 flex gap-3 text-xs">
              <Link
                href="/console/account"
                className="text-muted underline-offset-2 hover:text-ink hover:underline"
              >
                Your account
              </Link>
              <button
                className="text-muted underline-offset-2 hover:text-ink hover:underline"
                onClick={async () => {
                  await signOut();
                  router.replace("/console/login");
                }}
              >
                Sign out
              </button>
            </div>
          </div>
        </aside>

        <div className="flex min-w-0 flex-1 flex-col">
          <header className="flex h-12 items-center gap-2 border-b border-line bg-surface px-4 md:px-8">
            <nav aria-label="Console (mobile)" className="flex gap-2 overflow-x-auto md:hidden">
              {navFor(me.role).map((n) => (
                <Link
                  key={n.href}
                  href={n.href}
                  className={`whitespace-nowrap text-sm ${path === n.href ? "font-semibold text-ink" : "text-muted"}`}
                >
                  {n.label}
                </Link>
              ))}
            </nav>
            <div className="ml-auto">
              <ThemeToggle />
            </div>
            {!isAnalyticsOnly(me.role) && (
              <div className="relative">
                <button
                  className="btn-ghost relative min-h-9 px-2"
                  aria-expanded={bell}
                  aria-label={`Notifications, ${notes.unread} unread`}
                  onClick={() => setBell((b) => !b)}
                >
                  <svg
                    aria-hidden
                    viewBox="0 0 20 20"
                    className="h-5 w-5 text-muted"
                    fill="none"
                    stroke="currentColor"
                    strokeWidth="1.6"
                  >
                    <path
                      d="M10 2.5a5 5 0 0 0-5 5v3l-1.5 3h13L15 10.5v-3a5 5 0 0 0-5-5Z"
                      strokeLinejoin="round"
                    />
                    <path d="M8 16a2 2 0 0 0 4 0" strokeLinecap="round" />
                  </svg>
                  {notes.unread > 0 && (
                    <span
                      className="absolute -right-0.5 -top-0.5 min-w-4 rounded-full bg-brand px-1 text-center text-[10px] font-bold leading-4"
                      style={{ color: "var(--on-brand)" }}
                    >
                      {notes.unread}
                    </span>
                  )}
                </button>
                {bell && (
                  <ul className="absolute right-0 z-20 mt-2 max-h-[70vh] w-96 overflow-y-auto rounded-lg border border-line bg-surface shadow-lg">
                    {notes.items.length === 0 && (
                      <li className="px-4 py-3 text-sm text-muted">Nothing new.</li>
                    )}
                    {notes.items.map((n) => (
                      <li key={n.id}>
                        <button
                          onClick={() => void openNote(n)}
                          className={`block w-full px-4 py-3 text-left text-sm hover:bg-paper ${n.read ? "text-muted" : "font-semibold"}`}
                        >
                          {n.kind === "improvement.tested"
                            ? testedTitle(n)
                            : n.kind === "callback.created"
                              ? "New callback request"
                              : n.kind === "break_glass.started"
                                ? "⚠️ Platform admin opened personal data"
                                : "New handoff"}
                          {n.payload.reason && (
                            <span className="block truncate text-xs font-normal">{n.payload.reason}</span>
                          )}
                        </button>
                      </li>
                    ))}
                  </ul>
                )}
              </div>
            )}
          </header>
          {me.role === "super_admin" && <BreakGlassBar />}
          <main id="content" className="mx-auto w-full max-w-6xl flex-1 px-4 py-8 md:px-8">
            {children}
          </main>
        </div>
      </div>
    </MeContext.Provider>
  );
}
