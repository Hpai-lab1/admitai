"use client";

import { useRouter } from "next/navigation";
import QRCode from "qrcode";
import { useEffect, useState } from "react";
import { ApiError, request, saveTokens, type MfaChallenge, type Tokens } from "@/lib/api";
import { homeFor } from "@/lib/roles";
import { themeStyle } from "@/lib/theme";

export default function Login() {
  const router = useRouter();
  const [form, setForm] = useState({ tenant: "demo-college", email: "", password: "", code: "" });
  const [mfa, setMfa] = useState<MfaChallenge | null>(null);
  const [qr, setQr] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    if (mfa?.otpauth_uri) void QRCode.toDataURL(mfa.otpauth_uri, { margin: 1, width: 200 }).then(setQr);
  }, [mfa]);

  const done = (t: Tokens) => {
    saveTokens(t);
    router.replace(homeFor(t.role));
  };

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    setBusy(true);
    setError(null);
    try {
      if (!mfa) {
        const r = await request<Tokens | MfaChallenge>("/auth/login", {
          method: "POST",
          body: JSON.stringify({ tenant: form.tenant, email: form.email, password: form.password }),
        });
        if (r.status === "ok") done(r);
        else setMfa(r);
      } else {
        done(
          await request<Tokens>("/auth/mfa", {
            method: "POST",
            body: JSON.stringify({ mfa_token: mfa.mfa_token, code: form.code.replace(/\s/g, "") }),
          }),
        );
      }
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Sign-in failed");
    } finally {
      setBusy(false);
    }
  }

  const set = (k: keyof typeof form) => (e: React.ChangeEvent<HTMLInputElement>) =>
    setForm((f) => ({ ...f, [k]: e.target.value }));

  return (
    <main
      style={themeStyle({
        primary_color: "#E34A21",
        secondary_color: "#992361",
        accent_color: "#361C6A",
        font: "montserrat",
      })}
      className="grid min-h-screen place-items-center bg-paper px-4 font-brand"
    >
      <div className="card w-full max-w-md overflow-hidden">
        <div className="brand-gradient px-6 py-5">
          <p className="text-xs font-semibold uppercase tracking-wider opacity-90">AdmitAI</p>
          <h1 className="text-xl font-bold">Staff sign-in</h1>
        </div>
        <form onSubmit={submit} className="space-y-4 p-6">
          {error && (
            <p role="alert" className="rounded-lg bg-red-50 px-3 py-2 text-sm text-red-800">
              {error}
            </p>
          )}
          {!mfa ? (
            <>
              <div>
                <label htmlFor="tenant" className="label">
                  College code
                </label>
                <input id="tenant" className="field" value={form.tenant} onChange={set("tenant")} required />
              </div>
              <div>
                <label htmlFor="email" className="label">
                  E-mail
                </label>
                <input
                  id="email"
                  type="email"
                  autoComplete="username"
                  className="field"
                  value={form.email}
                  onChange={set("email")}
                  required
                />
              </div>
              <div>
                <label htmlFor="password" className="label">
                  Password
                </label>
                <input
                  id="password"
                  type="password"
                  autoComplete="current-password"
                  className="field"
                  value={form.password}
                  onChange={set("password")}
                  required
                />
              </div>
            </>
          ) : (
            <>
              {mfa.status === "mfa_enroll" ? (
                <div className="space-y-3 text-sm">
                  <p>
                    Admins must use two-step sign-in. Scan this code with an authenticator app (Google
                    Authenticator, Microsoft Authenticator…), then type the 6-digit code it shows.
                  </p>
                  {qr && (
                    // eslint-disable-next-line @next/next/no-img-element -- a data: URL QR code, nothing to optimise
                    <img src={qr} alt="QR code for your authenticator app" className="mx-auto h-48 w-48" />
                  )}
                  <p className="text-xs text-muted">
                    Can&apos;t scan? Enter this key: <code className="break-all font-mono">{mfa.secret}</code>
                  </p>
                </div>
              ) : (
                <p className="text-sm">Open your authenticator app and type the 6-digit code.</p>
              )}
              <div>
                <label htmlFor="code" className="label">
                  6-digit code
                </label>
                <input
                  id="code"
                  className="field tracking-widest"
                  inputMode="numeric"
                  autoComplete="one-time-code"
                  maxLength={8}
                  value={form.code}
                  onChange={set("code")}
                  autoFocus
                  required
                />
              </div>
            </>
          )}
          <button className="btn-primary w-full" disabled={busy}>
            {busy ? "Signing in…" : mfa ? "Verify" : "Sign in"}
          </button>
          {mfa && (
            <button type="button" className="btn-ghost w-full" onClick={() => setMfa(null)}>
              Start again
            </button>
          )}
        </form>
      </div>
    </main>
  );
}
