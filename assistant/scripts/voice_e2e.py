"""End-to-end LiveKit voice test (PRD F2.1, F2.2, F2.4, F2.5): a scripted visitor in a real room.

    source local.env.sh && cd assistant
    python scripts/voice_e2e.py --widget-key <public widget key> --out benchmarks/voice-e2e-<date>.json

Needs Module C (visitor session), Module B (POST /voice/token), the LiveKit server and the voice
worker (python -m assistant_api.voice_worker) running. The visitor:

1. starts a widget session with Module C and gets a room token from Module B, as the widget does;
2. joins the room, publishes a microphone track and, for each question, "speaks" it: Piper
   speech (SYNTHETIC: cleaner than a real caller, so accuracy here is a best case), resampled to
   16 kHz and sent in real time, then silence;
3. measures, on the visitor's side, end of speech -> first answer audio (F2.5: <= 3.5 s p95), live
   captions (F2.2), and for barge-in (F2.4) the time from starting to talk over the answer to
   the answer audio going silent (<= 300 ms).

Nothing is faked on the server side: real VAD, faster-whisper, engine, model gateway and Piper.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import platform
import statistics
import sys
import time
import uuid
from pathlib import Path

import httpx
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from assistant_api.voice import Speech  # noqa: E402
from traffic_cleanup import retire  # noqa: E402

QUESTIONS = [
    "What is the application fee?",
    "Is there a hostel on the campus?",
    "What is the COMEDK code?",
    "Can I pay the fees in cash?",
    "Is there negative marking in the entrance test?",
    "How long is the B.Tech programme?",
    "Can diploma holders apply for lateral entry?",
    "Is there a scholarship for students?",
    "What is the fee refund policy?",
    "Where is the main campus?",
]
BARGE_IN_QUESTION = "Tell me about the admission process for B.Tech."
BARGE_IN_WORDS = "Sorry, wait, one more question."
RATE = 16_000
FRAME = 320  # 20 ms
SILENT = 0.004  # mean absolute amplitude below this is silence


def to16k(pcm: bytes, rate: int) -> np.ndarray:
    x = np.frombuffer(pcm, np.int16).astype(np.float32)
    t = np.arange(0, len(x) / rate, 1 / RATE)
    return np.interp(t, np.arange(len(x)) / rate, x).astype(np.int16)


def pct(xs: list[float], p: float) -> float | None:
    if not xs:
        return None
    s = sorted(xs)
    return s[min(len(s) - 1, int(round(p * (len(s) - 1))))]


class Visitor:
    def __init__(self, room, source):
        self.room, self.source = room, source
        self.messages: list[tuple[float, dict]] = []
        self.loud_at: list[float] = []  # times of received non-silent assistant frames
        self.last_frame = 0.0

    async def say(self, samples: np.ndarray) -> float:
        """Send audio in real time; returns the time the last sample was sent."""
        from livekit import rtc

        for i in range(0, len(samples), FRAME):
            chunk = samples[i : i + FRAME]
            if len(chunk) < FRAME:
                chunk = np.pad(chunk, (0, FRAME - len(chunk)))
            await self.source.capture_frame(
                rtc.AudioFrame(chunk.astype(np.int16).tobytes(), RATE, 1, FRAME)
            )
        return time.monotonic()

    async def quiet(self, seconds: float) -> None:
        await self.say(np.zeros(int(RATE * seconds), np.int16))

    def since(self, t: float, kind: str) -> list[dict]:
        return [m for at, m in self.messages if at >= t and m.get("type") == kind]

    async def wait_for(self, t: float, kind: str, timeout: float) -> dict | None:
        end = time.monotonic() + timeout
        while time.monotonic() < end:
            found = self.since(t, kind)
            if found:
                return found[0]
            await self.quiet(
                0.1
            )  # keep the microphone track flowing, as a browser does
        return None


async def main_async(a) -> dict:
    from livekit import rtc

    models = Path(a.models)
    voice = Speech(str(models), "base", "en_US-lessac-medium")
    http = httpx.Client(timeout=30)
    s = http.post(
        f"{a.experience}/widget/session",
        json={"public_key": a.widget_key, "utm": {}, "referrer": None},
    )
    s.raise_for_status()
    visitor_token = s.json()["session_token"]
    tenant_id, session_id = s.json()["tenant"]["id"], s.json()["session_id"]
    conv = f"voice-e2e-{uuid.uuid4().hex[:8]}"
    tok = http.post(
        f"{a.assistant}/voice/token",
        json={"conversation_id": conv, "language": "en"},  # the widget's EN button
        headers={"Authorization": f"Bearer {visitor_token}"},
    )
    tok.raise_for_status()
    lk = tok.json().get("livekit")
    if not lk:
        raise SystemExit(
            "Module B returned no LiveKit token: set LIVEKIT_URL/KEY/SECRET"
        )

    room = rtc.Room()
    source = rtc.AudioSource(RATE, 1, queue_size_ms=60)
    v = Visitor(room, source)

    @room.on("data_received")
    def _(packet):
        if packet.topic == "admitai":
            v.messages.append((time.monotonic(), json.loads(bytes(packet.data))))

    @room.on("track_subscribed")
    def _(track, pub, participant):
        async def read():
            async for ev in rtc.AudioStream(track, sample_rate=RATE):
                x = np.frombuffer(ev.frame.data, np.int16)
                now = time.monotonic()
                v.last_frame = now
                if np.abs(x).mean() / 32768 > SILENT:
                    v.loud_at.append(now)

        asyncio.ensure_future(read())

    await room.connect(lk["url"], lk["token"])
    track = rtc.LocalAudioTrack.create_audio_track("mic", source)
    await room.local_participant.publish_track(
        track, rtc.TrackPublishOptions(source=rtc.TrackSource.SOURCE_MICROPHONE)
    )
    if not await v.wait_for(0, "state", 20):
        raise SystemExit("The voice worker did not join the room (is it running?)")

    rows = []
    for q in QUESTIONS[: a.limit]:
        pcm, rate = voice.speak_pcm(q)
        await v.quiet(0.6)
        t_start = time.monotonic()
        t_end = await v.say(to16k(pcm, rate))
        done = await v.wait_for(t_start, "done", 40)
        await v.quiet(0.3)
        loud = [t for t in v.loud_at if t > t_end]
        final = v.since(t_start, "caption")
        rows.append(
            {
                "question": q,
                "heard": next((c["text"] for c in final if c.get("final")), None),
                "partial_captions": sum(1 for c in final if not c.get("final")),
                "answer": " ".join(m["text"] for m in v.since(t_start, "answer")),
                "outcome": done and done.get("outcome"),
                # F2.5, measured by the visitor: end of speech -> first answer audio heard.
                "end_of_speech_to_audio_s": round(loud[0] - t_end, 3) if loud else None,
                "worker_timings": done and done.get("timings"),
            }
        )
        # let the answer finish playing before the next question
        while v.loud_at and time.monotonic() - v.loud_at[-1] < 0.8:
            await v.quiet(0.2)
        print(
            f"{rows[-1]['end_of_speech_to_audio_s']}s  {q!r} -> heard {rows[-1]['heard']!r}"
            f" [{rows[-1]['outcome']}]"
        )

    barge = []
    for _ in range(a.barge_ins):
        pcm, rate = voice.speak_pcm(BARGE_IN_QUESTION)
        await v.quiet(0.6)
        t_end = await v.say(to16k(pcm, rate))
        while not [t for t in v.loud_at if t > t_end]:
            if time.monotonic() - t_end > 30:
                break
            await v.quiet(0.05)
        await v.quiet(0.8)  # the assistant is speaking now
        pcm2, rate2 = voice.speak_pcm(BARGE_IN_WORDS)
        words = to16k(pcm2, rate2)
        lead = int(np.argmax(np.abs(words) > 300))  # first audible sample of our speech
        await v.say(words[:lead])
        t_talk = time.monotonic()
        await v.say(words[lead:])
        stopped = None
        # the answer audio has stopped once nothing loud arrives for 100 ms
        loud_after = [t for t in v.loud_at if t > t_talk]
        gaps = [t_talk] + loud_after
        for i in range(1, len(gaps)):
            if gaps[i] - gaps[i - 1] > 0.1:
                stopped = gaps[i - 1]
                break
        if stopped is None and loud_after:
            stopped = loud_after[-1]
        if stopped is None:
            stopped = t_talk
        msg = v.since(t_talk - 0.2, "barge_in")
        barge.append(
            {
                "visitor_measured_stop_ms": int((stopped - t_talk) * 1000),
                "worker_stop_ms": msg[0]["stop_ms"] if msg else None,
                "barge_in_signalled": bool(msg),
            }
        )
        await v.wait_for(time.monotonic(), "done", 40)
        await v.quiet(1.0)
        print("barge-in:", barge[-1])
    await room.disconnect()
    await asyncio.sleep(3)  # the last answer finishes and is saved before it is erased
    cleanup = retire(tenant_id, {conv: session_id})

    lat = [r["end_of_speech_to_audio_s"] for r in rows if r["end_of_speech_to_audio_s"]]
    return {
        "date": time.strftime("%Y-%m-%d"),
        "host": {"cpu_count": os.cpu_count(), "machine": platform.machine()},
        "audio": "synthetic (Piper en_US-lessac-medium), sent in real time over WebRTC",
        "settings": {
            "whisper_size": os.environ.get("WHISPER_SIZE", "base"),
            "answer_model": os.environ.get("ANSWER_MODEL"),
            "gateway": os.environ.get("LLM_BASE_URL"),
        },
        "summary": {
            "questions": len(rows),
            "answered": sum(1 for r in rows if r["outcome"] == "answered"),
            "end_of_speech_to_audio_s": {
                "p50": pct(lat, 0.5),
                "p95": pct(lat, 0.95),
                "max": max(lat) if lat else None,
                "target_p95": 3.5,
            },
            "partial_captions_per_question": round(
                statistics.mean(r["partial_captions"] for r in rows), 2
            )
            if rows
            else None,
            "barge_in_stop_ms": [b["visitor_measured_stop_ms"] for b in barge],
            "barge_in_target_ms": 300,
        },
        "rows": rows,
        "barge_ins": barge,
        # Test traffic: marked "operator" and erased in Modules B and C after the run.
        "cleanup": cleanup,
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--widget-key", required=True)
    ap.add_argument(
        "--experience",
        default=os.environ.get("EXPERIENCE_API_URL", "http://127.0.0.1:8200"),
    )
    ap.add_argument("--assistant", default="http://127.0.0.1:8150")
    ap.add_argument(
        "--models", default=os.environ.get("VOICE_MODEL_DIR", "../.local/models")
    )
    ap.add_argument("--limit", type=int, default=len(QUESTIONS))
    ap.add_argument("--barge-ins", type=int, default=3)
    ap.add_argument("--out", type=Path)
    a = ap.parse_args()
    report = asyncio.run(main_async(a))
    print(json.dumps(report["summary"], indent=1))
    if a.out:
        a.out.parent.mkdir(parents=True, exist_ok=True)
        a.out.write_text(
            json.dumps(report, ensure_ascii=False, indent=1), encoding="utf-8"
        )
        print("written", a.out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
