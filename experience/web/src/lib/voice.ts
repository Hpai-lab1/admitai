import { ASSISTANT_URL } from "./config";
import { tooMany, type HandoffRequest, type Source } from "./widget-api";

/** One push-to-talk turn answered by Module B (PRD F2). Audio goes only to the college's own
 *  servers: no browser speech service is used (Level 1 sends no data outside the lab). */
export type VoiceReply = {
  transcript: string;
  language: string;
  answer: string;
  outcome: string;
  offer_handoff: boolean;
  sources: Source[];
  message_id?: string;
  trace_id?: string;
  audio_wav_base64: string | null;
  events?: { handoff_requested?: HandoffRequest; consent_required?: unknown };
  timings: Record<string, number>;
};

export class VoiceError extends Error {
  constructor(
    public kind: "denied" | "unsupported" | "busy" | "failed",
    message: string,
  ) {
    super(message);
  }
}

/** Start recording; the returned function stops it and resolves with the recording. */
export async function record(): Promise<() => Promise<Blob>> {
  if (
    typeof navigator === "undefined" ||
    !navigator.mediaDevices?.getUserMedia ||
    typeof MediaRecorder === "undefined"
  )
    throw new VoiceError("unsupported", "unsupported");
  let stream: MediaStream;
  try {
    stream = await navigator.mediaDevices.getUserMedia({ audio: true });
  } catch {
    throw new VoiceError("denied", "denied");
  }
  const rec = new MediaRecorder(stream);
  const chunks: Blob[] = [];
  rec.ondataavailable = (e) => e.data.size && chunks.push(e.data);
  rec.start();
  return () =>
    new Promise<Blob>((resolve) => {
      rec.onstop = () => {
        stream.getTracks().forEach((tr) => tr.stop());
        resolve(new Blob(chunks, { type: rec.mimeType || "audio/webm" }));
      };
      rec.stop();
    });
}

export async function voiceTurn(
  sessionToken: string,
  conversationId: string,
  audio: Blob,
  lang: string,
): Promise<VoiceReply> {
  const tok = await fetch(`${ASSISTANT_URL}/voice/token`, {
    method: "POST",
    headers: { "Content-Type": "application/json", Authorization: `Bearer ${sessionToken}` },
    body: JSON.stringify({ conversation_id: conversationId }),
  });
  if (!tok.ok) throw new VoiceError("failed", "The voice session could not start.");
  const form = new FormData();
  form.append("token", (await tok.json()).token);
  form.append("language", lang);
  form.append("audio", audio, "question.webm");
  const res = await fetch(`${ASSISTANT_URL}/voice/turn`, { method: "POST", body: form });
  if (res.status === 429) throw new VoiceError("busy", await tooMany(res));
  if (res.status === 503) {
    const body = await res.json().catch(() => ({}));
    throw new VoiceError("busy", typeof body.detail === "string" ? body.detail : "Voice is busy.");
  }
  if (!res.ok) throw new VoiceError("failed", "The assistant could not answer by voice.");
  return (await res.json()) as VoiceReply;
}

/** Play a WAV reply. The returned function stops it at once (barge-in, PRD F2.4). */
export function play(base64Wav: string, onEnd: () => void): () => void {
  const audio = new Audio(`data:audio/wav;base64,${base64Wav}`);
  audio.onended = onEnd;
  void audio.play().catch(onEnd);
  return () => {
    audio.pause();
    onEnd();
  };
}
