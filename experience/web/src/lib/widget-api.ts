import { request } from "./api";
import { ASSISTANT_URL } from "./config";
import { SseParser } from "./sse";
import type { Theme } from "./theme";

export type TenantInfo = Theme & {
  id: string;
  name: string;
  assistant_name: string;
  greeting: string;
  starter_questions: string[];
  topics: string[];
  departments: string[];
  consent_text_version: string;
  privacy_notice_url: string | null;
  office_hours: string;
  response_time_hours: number;
  apply_url: string | null;
  announcement: string | null;
};
export type WidgetSession = {
  session_id: string;
  session_token: string;
  expires_at: string;
  tenant: TenantInfo;
};
export type Source = { title: string; academic_year?: string; url?: string; heading_path?: string };
/** Module B's side events (PRD 21.4): who should take over, when to ask for contact, minors. */
export type HandoffRequest = { queue: "admission" | "faculty"; department: string | null; reason: string };
export type LeadPrompt = { score: number; band: string; program_interest: string[]; is_minor: boolean };
export type AnswerDone = {
  message_id: string;
  conversation_id: string;
  trace_id: string;
  confidence: number;
  offer_handoff: boolean;
  sources: Source[];
  language?: string;
  outcome?: string;
  handoff?: HandoffRequest;
  lead_prompt?: LeadPrompt;
  guardian_required?: boolean;
};

export const startSession = (
  publicKey: string,
  utm: Record<string, string>,
  referrer: string | null,
  previewToken?: string | null,
) =>
  request<WidgetSession>(
    "/widget/session",
    { method: "POST", body: JSON.stringify({ public_key: publicKey, utm, referrer }) },
    previewToken ?? undefined,
  );

export function visitor<T>(token: string, path: string, body: unknown): Promise<T> {
  return request<T>(path, { method: "POST", body: JSON.stringify(body) }, token);
}

/** A 429 problem response (PRD 12.3) as one sentence, with the wait from Retry-After. */
export async function tooMany(res: Response): Promise<string> {
  const body = (await res.json().catch(() => ({}))) as { detail?: unknown };
  const detail = typeof body.detail === "string" ? body.detail : "Too many questions at once.";
  const wait = Number(res.headers.get("Retry-After"));
  return wait > 0 ? `${detail} (about ${wait} s)` : detail;
}

/** The connection broke after the question was sent: the answer may still be finished and saved
 *  by Module B, so the widget tries `recoverAnswer` instead of losing it (PRD F1 error handling). */
export class StreamCut extends Error {}

/** Ask Module B; calls onToken for each streamed word, resolves with the final metadata. */
export async function ask(
  conversationId: string,
  text: string,
  sessionToken: string,
  onToken: (t: string) => void,
  language: string = "en",
  signal?: AbortSignal,
): Promise<AnswerDone> {
  const res = await fetch(`${ASSISTANT_URL}/chat/${encodeURIComponent(conversationId)}/messages`, {
    method: "POST",
    headers: { "Content-Type": "application/json", Authorization: `Bearer ${sessionToken}` },
    body: JSON.stringify({ text, language }),
    signal,
  }).catch((e: unknown) => {
    if (signal?.aborted) throw e;
    throw new StreamCut("The connection was lost.");
  });
  if (res.status === 429) throw new Error(await tooMany(res));
  if (!res.ok || !res.body) throw new Error("The assistant is not available right now.");
  const reader = res.body.getReader();
  const decoder = new TextDecoder();
  const parser = new SseParser();
  let done: AnswerDone | null = null;
  const side: Pick<AnswerDone, "handoff" | "lead_prompt" | "guardian_required"> = {};
  for (;;) {
    const { value, done: end } = await reader.read().catch(() => {
      throw new StreamCut("The connection was lost.");
    });
    if (end) break;
    for (const ev of parser.push(decoder.decode(value, { stream: true }))) {
      const data = JSON.parse(ev.data);
      if (ev.event === "token") onToken(data.text);
      else if (ev.event === "done") done = data as AnswerDone;
      else if (ev.event === "handoff_requested") side.handoff = data as HandoffRequest;
      else if (ev.event === "lead_prompt") side.lead_prompt = data as LeadPrompt;
      else if (ev.event === "consent_required") side.guardian_required = true;
    }
  }
  if (!done) throw new StreamCut("The answer was cut off.");
  return { ...(done as AnswerDone), ...side };
}

export type RecoveredAnswer = Pick<AnswerDone, "message_id" | "trace_id" | "sources" | "offer_handoff"> & {
  text: string;
};

/** After a StreamCut: read the finished answer to this question back from Module B, waiting while
 *  it is still being written. Null if it never appears (the question then needs asking again). */
export async function recoverAnswer(
  conversationId: string,
  question: string,
  sessionToken: string,
  { tries = 10, waitMs = 2000 }: { tries?: number; waitMs?: number } = {},
): Promise<RecoveredAnswer | null> {
  for (let i = 0; i < tries; i++) {
    if (i) await new Promise((r) => setTimeout(r, waitMs));
    const res = await fetch(`${ASSISTANT_URL}/chat/${encodeURIComponent(conversationId)}/last`, {
      headers: { Authorization: `Bearer ${sessionToken}` },
    }).catch(() => null);
    if (!res?.ok) continue;
    const last = (await res.json()) as RecoveredAnswer & { question: string | null };
    if (last.question?.trim() === question.trim()) return last;
  }
  return null;
}

/** Fire-and-forget conversation event (PRD F8.3 implicit signals; 3.5 metric counts). */
export function logEvent(token: string, conversationId: string, kind: string, messageId?: string): void {
  void visitor(token, "/conversation-events", {
    conversation_id: conversationId,
    kind,
    message_id: messageId ?? null,
  }).catch(() => {});
}

/** Quick replies after an answer (PRD F1.5): the college's suggested questions not asked yet. */
export function quickReplies(suggested: string[], asked: string[], n = 3): string[] {
  const seen = new Set(asked.map((q) => q.trim().toLowerCase()));
  return suggested.filter((q) => !seen.has(q.trim().toLowerCase())).slice(0, n);
}

/** Long answers collapse after 12 lines (PRD 13.4). Estimated from the text (about 45 characters
 *  per line in the 400 px widget), so it does not depend on layout measurement. */
export function isLong(text: string, maxLines = 12, perLine = 45): boolean {
  const lines = text.split("\n").reduce((n, p) => n + Math.max(1, Math.ceil(p.length / perLine)), 0);
  return lines > maxLines;
}
