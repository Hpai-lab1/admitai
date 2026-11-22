import { ASSISTANT_URL } from "./config";

/** A running Retell AI voice call (Module B's retell.py). Stop ends it. */
export type RetellCall = { stop: () => void };

/** One line of the live call transcript: who spoke and what Retell heard or the assistant said. */
export type CallLine = { role: "user" | "agent"; content: string };

/** The line to show while the call runs: the latest thing said, if any. */
export function latestLine(transcript: unknown): CallLine | null {
  if (!Array.isArray(transcript)) return null;
  for (let i = transcript.length - 1; i >= 0; i--) {
    const t = transcript[i] as Partial<CallLine>;
    if ((t.role === "user" || t.role === "agent") && t.content?.trim()) {
      return { role: t.role, content: t.content.trim() };
    }
  }
  return null;
}

/** Every line of the call so far, in order, for the chat history (empty lines left out). */
export function allLines(transcript: unknown): CallLine[] {
  if (!Array.isArray(transcript)) return [];
  const out: CallLine[] = [];
  for (const item of transcript) {
    const t = item as Partial<CallLine>;
    if ((t.role === "user" || t.role === "agent") && t.content?.trim()) {
      out.push({ role: t.role, content: t.content.trim() });
    }
  }
  return out;
}

type Hooks = {
  onAgentTalking: (talking: boolean) => void;
  onLine: (line: CallLine) => void;
  /** The whole transcript so far, so the call is also written into the chat. */
  onTranscript?: (lines: CallLine[]) => void;
  onEnded: () => void;
  onError: (message: string) => void;
};

/**
 * Start a Retell call when the college has Retell configured; null when it has not (the widget
 * then uses push-to-talk). Retell listens and speaks; every answer comes from AdmitAI's engine.
 */
export async function startRetellCall(
  sessionToken: string,
  conversationId: string,
  lang: string,
  hooks: Hooks,
): Promise<RetellCall | null> {
  const res = await fetch(`${ASSISTANT_URL}/voice/token`, {
    method: "POST",
    headers: { "Content-Type": "application/json", Authorization: `Bearer ${sessionToken}` },
    body: JSON.stringify({ conversation_id: conversationId, language: lang, call: true }),
  });
  if (!res.ok) return null;
  const body = (await res.json()) as {
    retell?: {
      access_token: string;
      call_id: string;
      transport?: "gateway" | "livekit" | null;
      ice_servers?: RTCIceServer[];
    };
  };
  if (!body.retell) return null;
  const r = body.retell;
  const { RetellWebClient } = await import("retell-client-js-sdk");
  const client = new RetellWebClient();
  client.on("agent_start_talking", () => hooks.onAgentTalking(true));
  client.on("agent_stop_talking", () => hooks.onAgentTalking(false));
  // Live transcript: shows the visitor what was heard, and that the assistant is answering.
  client.on("update", (u: { transcript?: unknown }) => {
    const line = latestLine(u?.transcript);
    if (line) hooks.onLine(line);
    hooks.onTranscript?.(allLines(u?.transcript));
  });
  // Newer Retell sessions send the transcript as its own event; take it the same way.
  client.on("transcript", (transcript: unknown) => {
    const line = latestLine(transcript);
    if (line) hooks.onLine(line);
    hooks.onTranscript?.(allLines(transcript));
  });
  client.on("call_ended", () => hooks.onEnded());
  client.on("error", (e: unknown) => {
    hooks.onError(e instanceof Error ? e.message : "Voice call failed");
    client.stopCall();
  });
  // A server-created v3 web call: Retell's notice says to pass all four fields through.
  await client.startCall({
    accessToken: r.access_token,
    callId: r.call_id,
    transport: r.transport ?? undefined,
    iceServers: r.ice_servers,
  });
  return { stop: () => client.stopCall() };
}
