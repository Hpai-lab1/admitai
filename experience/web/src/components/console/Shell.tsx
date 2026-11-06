"use client";

import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import { createContext, useContext, useEffect, useState } from "react";
import { ApiError, hasSession, signOut, staff, type Me } from "@/lib/api";
import { navFor, ROLE_LABEL } from "@/lib/roles";
import { themeStyle } from "@/lib/theme";

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
  const icon = { system: "🖥", light: "☀️", dark: "🌙" }[mode];
  return (
    <button
      className="btn-ghost"
      aria-label={`Colour theme: ${mode}. Switch to ${next[mode]}`}
      title={`Theme: ${mode}`}
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
      <span aria-hidden>{icon}</span>
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
  payload: { handoff_id?: string; reason?: string };
  created_at: string;
  read: boolean;
};

export function Shell({ children }: { children: React.ReactNode }) {
  const path = usePathname();
  const router = useRouter();
  const [me, setMe] = useState<Me | null>(null);
  const [notes, setNotes] = useState<{ unread: number; items: Notification[] }>({ unread: 0, items: [] });
  const [bell, setBell] = useState(false);
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
    if (!me || me.role === "marketing_manager") return;
    const poll = () =>
      staff<{ unread: number; items: Notification[] }>("/notifications?limit=10")
        .then(setNotes)
        .catch(() => {});
    poll();
    const t = setInterval(poll, 30000);
    return () => clearInterval(t);
  }, [me]);

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
  };

  return (
    <MeContext.Provider value={me}>
      <div style={themeStyle(me.tenant.theme)} className="flex min-h-screen bg-paper font-brand text-ink">
        <a
          href="#content"
          className="sr-only-focusable absolute left-2 top-2 z-50 rounded bg-surface px-3 py-2"
        >
          Skip to content
        </a>
        <aside className="hidden w-60 shrink-0 flex-col bg-accent text-white md:flex">
          <div className="brand-gradient px-5 py-5">
            <p className="text-xs font-semibold uppercase tracking-wider opacity-90">AdmitAI console</p>
            <p className="mt-1 font-bold leading-tight">{me.tenant.name}</p>
          </div>
          <nav aria-label="Console" className="flex-1 px-3 py-4">
            <ul className="space-y-1">
              {navFor(me.role).map((n) => {
                const active =
                  n.href === "/console"
                    ? path === "/console" || path.startsWith("/console/handoffs")
                    : path.startsWith(n.href);
                return (
                  <li key={n.href}>
                    <Link
                      href={n.href}
                      aria-current={active ? "page" : undefined}
                      className={`block rounded-lg px-3 py-2 text-sm font-semibold ${active ? "bg-white text-[rgb(var(--accent))]" : "hover:bg-white/10"}`}
                    >
                      {n.label}
                    </Link>
                  </li>
                );
              })}
            </ul>
          </nav>
          <div className="border-t border-white/15 px-5 py-4 text-sm">
            <p className="font-semibold">{me.full_name}</p>
            <p className="text-xs opacity-80">
              {ROLE_LABEL[me.role]}
              {me.departments.length ? ` · ${me.departments.join(", ")}` : ""}
            </p>
            <button
              className="mt-3 text-xs underline opacity-90 hover:opacity-100"
              onClick={async () => {
                await signOut();
                router.replace("/console/login");
              }}
            >
              Sign out
            </button>
          </div>
        </aside>

        <div className="flex min-w-0 flex-1 flex-col">
          <header className="flex items-center gap-3 border-b border-line bg-surface px-4 py-2 md:px-8">
            <nav aria-label="Console (mobile)" className="flex gap-2 overflow-x-auto md:hidden">
              {navFor(me.role).map((n) => (
                <Link
                  key={n.href}
                  href={n.href}
                  className="whitespace-nowrap text-sm font-semibold text-accent"
                >
                  {n.label}
                </Link>
              ))}
            </nav>
            <div className="ml-auto">
              <ThemeToggle />
            </div>
            {me.role !== "marketing_manager" && (
              <div className="relative">
                <button
                  className="btn-ghost relative"
                  aria-expanded={bell}
                  aria-label={`Notifications, ${notes.unread} unread`}
                  onClick={() => setBell((b) => !b)}
                >
                  🔔
                  {notes.unread > 0 && (
                    <span className="absolute -right-0.5 -top-0.5 rounded-full bg-brand px-1.5 text-[10px] font-bold text-white">
                      {notes.unread}
                    </span>
                  )}
                </button>
                {bell && (
                  <ul className="absolute right-0 z-20 mt-2 w-80 overflow-hidden rounded-xl border border-line bg-surface shadow-lg">
                    {notes.items.length === 0 && (
                      <li className="px-4 py-3 text-sm text-muted">Nothing new.</li>
                    )}
                    {notes.items.map((n) => (
                      <li key={n.id}>
                        <button
                          onClick={() => void openNote(n)}
                          className={`block w-full px-4 py-3 text-left text-sm hover:bg-paper ${n.read ? "text-muted" : "font-semibold"}`}
                        >
                          {n.kind === "callback.created"
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
