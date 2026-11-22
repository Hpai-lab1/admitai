// PROPOSAL FOR MODULE C (not built here): a LiveKit client for the widget, meant for
// experience/web/src/lib/livevoice.ts. Needs `livekit-client` 2.23.0. See CONTRACT-PROPOSAL.md,
// "Needed from Module C". The imports below are the widget's own modules.
import { ASSISTANT_URL } from "./config";
import type { Source } from "./widget-api";

/** Hands-free voice over WebRTC (PRD F2.1, 9.3): the browser joins the conversation's LiveKit
 *  room and Module B's voice worker listens, captions, answers and speaks. Audio goes only to the
 *  college's own LiveKit server. When Module B returns no room (LiveKit not set up), the caller
 *  falls back to push-to-talk over HTTP. */

export type LiveState = "idle" | "listening" | "thinking" | "speaking";

export type LiveHandlers = {
  onState: (s: LiveState) => void;
  /** Live caption while the visitor speaks (final false), then the transcript (final true). */
  onCaption: (text: string, final: boolean) => void;
  /** One checked sentence of the answer, as it is spoken. */
  onAnswer: (text: string) => void;
  onDone: (d: { outcome?: string; sources?: Source[]; message_id?: string; offer_handoff?: boolean }) => void;
  onBargeIn: () => void;
  onError: (kind: string) => void;
};

export type LiveVoice = {
  setLanguage: (lang: string) => void;
  stop: () => Promise<void>;
};

export type VoiceTokenReply = {
  token: string;
  expires_in: number;
  /** PRD F2.6: "off", "offered" (the widget may ask), "on" (this visitor consented). */
  audio_recording?: "off" | "offered" | "on";
  audio_days?: number | null;
  livekit?: { url: string; room: string; token: string };
};

export async function voiceToken(
  sessionToken: string,
  conversationId: string,
  lang: string,
  audioConsent: boolean,
): Promise<VoiceTokenReply> {
  const r = await fetch(`${ASSISTANT_URL}/voice/token`, {
    method: "POST",
    headers: { "Content-Type": "application/json", Authorization: `Bearer ${sessionToken}` },
    body: JSON.stringify({
      conversation_id: conversationId,
      language: ["en", "hi", "kn"].includes(lang) ? lang : null,
      audio_consent: audioConsent,
    }),
  });
  if (!r.ok) throw new Error("voice token");
  return (await r.json()) as VoiceTokenReply;
}

/** A data message from the voice worker (topic "admitai") -> the matching handler. */
export function dispatch(raw: Uint8Array, h: LiveHandlers): void {
  let m: Record<string, unknown>;
  try {
    m = JSON.parse(new TextDecoder().decode(raw));
  } catch {
    return;
  }
  switch (m.type) {
    case "state":
      if (["idle", "listening", "thinking", "speaking"].includes(m.state as string))
        h.onState(m.state as LiveState);
      break;
    case "caption":
      h.onCaption(String(m.text ?? ""), m.final === true);
      break;
    case "answer":
      h.onAnswer(String(m.text ?? ""));
      break;
    case "done":
      h.onDone(m as Parameters<LiveHandlers["onDone"]>[0]);
      break;
    case "barge_in":
      h.onBargeIn();
      break;
    case "error":
      h.onError(String(m.error ?? "error"));
      break;
  }
}

export async function startLiveVoice(reply: VoiceTokenReply, h: LiveHandlers): Promise<LiveVoice | null> {
  if (!reply.livekit) return null;
  const lk = await import("livekit-client");
  const room = new lk.Room({ adaptiveStream: false, dynacast: false });
  const players: HTMLMediaElement[] = [];
  // The worker says hello with a "state" message once it has joined. If it never comes (worker
  // down or busy), leave the room and let the caller fall back to push-to-talk.
  let heard: () => void = () => {};
  const workerJoined = new Promise<boolean>((resolve) => {
    heard = () => resolve(true);
    setTimeout(() => resolve(false), 5000);
  });
  room.on(lk.RoomEvent.DataReceived, (payload: Uint8Array, _p?: unknown, _k?: unknown, topic?: string) => {
    if (topic !== "admitai") return;
    heard();
    dispatch(payload, h);
  });
  room.on(lk.RoomEvent.TrackSubscribed, (track: { kind: string; attach: () => HTMLMediaElement }) => {
    if (track.kind !== "audio") return;
    const el = track.attach();
    el.hidden = true;
    document.body.appendChild(el);
    players.push(el);
  });
  room.on(lk.RoomEvent.Disconnected, () => h.onState("idle"));
  await room.connect(reply.livekit.url, reply.livekit.token);
  if (!(await workerJoined)) {
    await room.disconnect();
    return null;
  }
  // The browser's echo cancellation keeps the assistant's own voice from counting as barge-in.
  await room.localParticipant.setMicrophoneEnabled(true, {
    echoCancellation: true,
    noiseSuppression: true,
    autoGainControl: true,
  });
  const send = (msg: object) =>
    room.localParticipant.publishData(new TextEncoder().encode(JSON.stringify(msg)), {
      reliable: true,
      topic: "admitai",
    });
  return {
    setLanguage: (lang) => void send({ type: "language", lang }),
    stop: async () => {
      await room.disconnect();
      players.forEach((el) => el.remove());
    },
  };
}
