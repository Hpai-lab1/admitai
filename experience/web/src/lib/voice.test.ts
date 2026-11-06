import { afterEach, describe, expect, it, vi } from "vitest";
import { VoiceError, record, voiceTurn } from "./voice";

const json = (body: unknown, status = 200) =>
  new Response(JSON.stringify(body), { status, headers: { "content-type": "application/json" } });

afterEach(() => vi.unstubAllGlobals());

describe("voice", () => {
  it("gets a voice token, then sends the recording to the college's own server", async () => {
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(json({ token: "vt" }))
      .mockResolvedValueOnce(
        json({ transcript: "Is there a hostel?", answer: "Yes [1].", audio_wav_base64: null }),
      );
    vi.stubGlobal("fetch", fetchMock);
    const r = await voiceTurn("session", "conv-1", new Blob(["x"], { type: "audio/webm" }), "en");
    expect(r.transcript).toBe("Is there a hostel?");
    const [first, second] = fetchMock.mock.calls as [string, RequestInit][];
    expect(first?.[0]).toMatch(/\/voice\/token$/);
    expect(second?.[0]).toMatch(/\/voice\/turn$/);
    const form = second?.[1].body as FormData;
    expect(form.get("token")).toBe("vt");
  });

  it("reports a busy voice service so the widget can suggest chat", async () => {
    vi.stubGlobal(
      "fetch",
      vi
        .fn()
        .mockResolvedValueOnce(json({ token: "vt" }))
        .mockResolvedValueOnce(json({ detail: "Voice is busy right now." }, 503)),
    );
    await expect(voiceTurn("s", "c", new Blob(["x"]), "en")).rejects.toMatchObject({ kind: "busy" });
  });

  it("explains when the microphone is blocked", async () => {
    vi.stubGlobal("MediaRecorder", class {});
    vi.stubGlobal("navigator", { mediaDevices: { getUserMedia: () => Promise.reject(new Error("no")) } });
    await expect(record()).rejects.toBeInstanceOf(VoiceError);
    await expect(record()).rejects.toMatchObject({ kind: "denied" });
  });
});
