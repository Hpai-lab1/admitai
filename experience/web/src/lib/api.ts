import { API_URL } from "./config";
import type { Role } from "./roles";
import type { Theme } from "./theme";

export class ApiError extends Error {
  constructor(
    public status: number,
    message: string,
  ) {
    super(message);
  }
}

export type Tokens = {
  status: "ok";
  access_token: string;
  refresh_token: string;
  expires_in: number;
  role: Role;
  name: string;
  departments: string[];
};
export type MfaChallenge = {
  status: "mfa_required" | "mfa_enroll";
  mfa_token: string;
  otpauth_uri: string | null;
  secret: string | null;
};
export type Me = {
  id: string;
  email: string;
  full_name: string;
  role: Role;
  departments: string[];
  mfa_enabled: boolean;
  tenant: { id: string; slug: string; name: string; public_key: string; theme: Theme };
};

/** FastAPI errors are {detail: string} or {detail: [{msg}]}; turn either into one sentence. */
export function errorMessage(body: unknown, fallback: string): string {
  const detail = (body as { detail?: unknown } | null)?.detail;
  if (typeof detail === "string") return detail;
  if (Array.isArray(detail) && detail[0] && typeof detail[0].msg === "string") {
    return String(detail[0].msg).replace(/^Value error, /, "");
  }
  return fallback;
}

export async function request<T>(path: string, init: RequestInit = {}, token?: string): Promise<T> {
  const headers = new Headers(init.headers);
  // FormData (file uploads) sets its own multipart content type.
  if (init.body && !(init.body instanceof FormData) && !headers.has("Content-Type"))
    headers.set("Content-Type", "application/json");
  if (token) headers.set("Authorization", `Bearer ${token}`);
  let res: Response;
  try {
    res = await fetch(`${API_URL}${path}`, { ...init, headers });
  } catch {
    throw new ApiError(0, "Can't reach the server. Check your connection and try again.");
  }
  if (res.status === 204) return undefined as T;
  const type = res.headers.get("content-type") ?? "";
  const body = type.includes("json") ? await res.json() : await res.text();
  if (!res.ok) throw new ApiError(res.status, errorMessage(body, `Request failed (${res.status})`));
  return body as T;
}

// ------------------------------------------------------------------ staff session
const KEY = "admitai.staff";
type Stored = { access: string; refresh: string };

function load(): Stored | null {
  try {
    const raw = sessionStorage.getItem(KEY);
    return raw ? (JSON.parse(raw) as Stored) : null;
  } catch {
    return null;
  }
}

export function saveTokens(t: Tokens): void {
  try {
    sessionStorage.setItem(KEY, JSON.stringify({ access: t.access_token, refresh: t.refresh_token }));
  } catch {
    /* private mode: the session simply won't survive a reload */
  }
}

/** The signed-in staff member's access token: lets the console's preview open a college's widget
 *  before launch (PRD F10.4 sandbox preview). */
export function staffAccessToken(): string | null {
  return load()?.access ?? null;
}

export function hasSession(): boolean {
  return load() !== null;
}

export async function signOut(): Promise<void> {
  const s = load();
  try {
    sessionStorage.removeItem(KEY);
  } catch {
    /* ignore */
  }
  if (s)
    await request("/auth/logout", {
      method: "POST",
      body: JSON.stringify({ refresh_token: s.refresh }),
    }).catch(() => {});
}

let refreshing: Promise<boolean> | null = null;

async function refresh(): Promise<boolean> {
  const s = load();
  if (!s) return false;
  refreshing ??= request<Tokens>("/auth/refresh", {
    method: "POST",
    body: JSON.stringify({ refresh_token: s.refresh }),
  })
    .then((t) => {
      saveTokens(t);
      return true;
    })
    .catch(() => false)
    .finally(() => {
      refreshing = null;
    });
  return refreshing;
}

/** Call the API as the signed-in staff member; renews the 15-minute access token once if needed. */
export async function staff<T>(path: string, init: RequestInit = {}): Promise<T> {
  const s = load();
  if (!s) throw new ApiError(401, "Sign in again");
  try {
    return await request<T>(path, init, s.access);
  } catch (e) {
    if (e instanceof ApiError && e.status === 401 && (await refresh())) {
      return request<T>(path, init, load()?.access);
    }
    throw e;
  }
}

/** Download a CSV (or any file) that needs the staff token. */
export async function staffDownload(path: string, filename: string): Promise<void> {
  const s = load();
  const res = await fetch(`${API_URL}${path}`, { headers: { Authorization: `Bearer ${s?.access ?? ""}` } });
  if (!res.ok) throw new ApiError(res.status, "Download failed");
  const url = URL.createObjectURL(await res.blob());
  const a = Object.assign(document.createElement("a"), { href: url, download: filename });
  a.click();
  URL.revokeObjectURL(url);
}
