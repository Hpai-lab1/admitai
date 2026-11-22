"""Speech-to-text benchmark (PRD 7.5, F2.2): Whisper model sizes on the CPU, as Module B runs them.

    source local.env.sh && cd assistant
    python scripts/stt_benchmark.py --sizes base,small --out benchmarks/stt-<date>.json

Test audio is SYNTHETIC: each question is spoken by a Piper voice (English: en_US-lessac-medium,
Hindi: hi_IN-priyamvada-medium when present in VOICE_MODEL_DIR/piper). Synthetic speech is cleaner
than a real caller, so word error rates here are a lower bound; the PRD acceptance test (50 recorded
English questions, <= 10% WER in a quiet room) still needs real recordings. There is no Piper voice
for Kannada, so Kannada cannot be measured this way at all.

Transcription goes through assistant_api.voice.Speech (CPU, int8, VAD filter, the vocabulary
prompt), i.e. exactly what /voice/turn does.
"""

from __future__ import annotations

import argparse
import io
import json
import os
import platform
import re
import sys
import time
import wave
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from assistant_api.voice import Speech  # noqa: E402

ENGLISH = [
    "What is the fee for B.Tech Computer Science and Engineering?",
    "Is there a hostel at the technology campus?",
    "What is the COMEDK code for the university?",
    "When does the application for the entrance test begin?",
    "How many seats are there in the MBA programme?",
    "Can diploma holders apply for lateral entry?",
    "What is the eligibility for B.Des Fashion Design?",
    "Is there a scholarship for orphan students?",
    "Can I pay the fees in cash?",
    "What is the fee refund policy?",
    "How long is the BPT course?",
    "What careers can B.Tech Robotics lead to?",
    "Is there negative marking in the entrance test?",
    "What is the application fee?",
    "Which entrance exams are accepted for M.Tech?",
    "Where is the main campus?",
    "Does the university provide education loans?",
    "What is the intake for BDS?",
    "Are original documents required during admission?",
    "Can I talk to someone from the admissions office?",
]
HINDI = [
    "बी टेक की फीस कितनी है?",
    "क्या हॉस्टल की सुविधा है?",
    "आवेदन शुल्क कितना है?",
    "प्रवेश परीक्षा कब होती है?",
    "क्या छात्रवृत्ति मिलती है?",
    "एमबीए में कितनी सीटें हैं?",
    "क्या मैं किस्तों में फीस दे सकता हूँ?",
    "कॉलेज कहाँ है?",
]
# Hinglish, spoken by the English voice: a rough stand-in for code-mixed speech.
HINGLISH = [
    "B.Tech CSE ki fees kitni hai?",
    "Hostel ka kharcha kitna hai?",
    "MBA ke liye kaunsa exam chahiye?",
    "Scholarship milegi kya?",
]


def norm(text: str) -> list[str]:
    t = text.lower()
    t = re.sub(r"\b([a-z])\.(?=[a-z])", r"\1", t)  # b.tech -> btech
    # keep Devanagari whole: Python's \w excludes its vowel signs, which would split every word
    t = re.sub(r"[^\w\s\u0900-\u0963\u0966-\u097F]", " ", t)
    return t.split()


def wer(ref: str, hyp: str) -> float:
    r, h = norm(ref), norm(hyp)
    d = list(range(len(h) + 1))
    for i in range(1, len(r) + 1):
        prev, d[0] = d[0], i
        for j in range(1, len(h) + 1):
            cur = d[j]
            d[j] = min(d[j] + 1, d[j - 1] + 1, prev + (r[i - 1] != h[j - 1]))
            prev = cur
    return d[len(h)] / max(1, len(r))


def pcm(wav_bytes: bytes) -> tuple[np.ndarray, int]:
    with wave.open(io.BytesIO(wav_bytes)) as w:
        rate = w.getframerate()
        x = np.frombuffer(w.readframes(w.getnframes()), dtype=np.int16).astype(
            np.float32
        )
    return x, rate


def to_wav(x: np.ndarray, rate: int) -> bytes:
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(np.clip(x, -32768, 32767).astype(np.int16).tobytes())
    return buf.getvalue()


def with_noise(wav_bytes: bytes, snr_db: float, seed: int = 7) -> bytes:
    x, rate = pcm(wav_bytes)
    noise = np.random.default_rng(seed).normal(0, 1, len(x))
    power = np.mean(x**2) / (10 ** (snr_db / 10))
    return to_wav(x + noise * np.sqrt(power), rate)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--sizes", default="base,small")
    ap.add_argument(
        "--models", default=os.environ.get("VOICE_MODEL_DIR", "../.local/models")
    )
    ap.add_argument("--out", type=Path)
    a = ap.parse_args()
    models = Path(a.models)
    en_voice = Speech(str(models), "base", "en_US-lessac-medium")
    hi_present = (models / "piper" / "hi_IN-priyamvada-medium.onnx").exists()
    hi_voice = (
        Speech(str(models), "base", "hi_IN-priyamvada-medium") if hi_present else None
    )

    # (expected language, group, reference, audio, language passed to the model). The widget passes
    # the language the visitor chose (EN / हिं / ಕ); None means automatic detection.
    clips = [("en", "english", q, en_voice.speak(q), None) for q in ENGLISH]
    clips += [
        ("en", "english_noise_10db", q, with_noise(en_voice.speak(q), 10), None)
        for q in ENGLISH[:10]
    ]
    clips += [("hi", "hinglish_auto", q, en_voice.speak(q), None) for q in HINGLISH]
    if hi_voice:
        hindi = [(q, hi_voice.speak(q)) for q in HINDI]
        clips += [("hi", "hindi_auto", q, audio, None) for q, audio in hindi]
        clips += [("hi", "hindi_chosen", q, audio, "hi") for q, audio in hindi]
    _, rate = pcm(clips[0][3])
    silence = to_wav(np.zeros(rate * 3, dtype=np.float32), rate)

    report: dict = {
        "date": time.strftime("%Y-%m-%d"),
        "host": {
            "cpu_count": os.cpu_count(),
            "machine": platform.machine(),
            "python": platform.python_version(),
        },
        "settings": {
            "device": "cpu",
            "compute_type": "int8",
            "beam_size": 1,
            "vad_filter": True,
        },
        "audio": "synthetic (Piper); a lower bound for real speech",
        "results": {},
    }
    for size in a.sizes.split(","):
        stt = Speech(str(models), size, "en_US-lessac-medium")
        t0 = time.perf_counter()
        stt._whisper()
        load_s = time.perf_counter() - t0
        stt.transcribe(clips[0][3])  # warm-up, not counted
        rows = []
        for lang, group, ref, audio, chosen in clips:
            seconds = len(pcm(audio)[0]) / rate
            t = time.perf_counter()
            c = time.process_time()
            out = stt.transcribe(audio, language=chosen)
            wall, cpu = time.perf_counter() - t, time.process_time() - c
            rows.append(
                {
                    "group": group,
                    "ref": ref,
                    "hyp": out.text,
                    "lang_expected": lang,
                    "lang_detected": out.language,
                    "wer": round(wer(ref, out.text), 3),
                    "audio_s": round(seconds, 2),
                    "latency_s": round(wall, 3),
                    "cpu_s": round(cpu, 3),
                }
            )
        s_out = stt.transcribe(silence)
        groups = {}
        for g in sorted({r["group"] for r in rows}):
            rs = [r for r in rows if r["group"] == g]
            lat = sorted(r["latency_s"] for r in rs)
            groups[g] = {
                "n": len(rs),
                "wer": round(sum(r["wer"] for r in rs) / len(rs), 3),
                "language_correct": sum(
                    r["lang_detected"] == r["lang_expected"] for r in rs
                ),
                "latency_p50_s": lat[len(lat) // 2],
                "latency_p95_s": lat[
                    min(len(lat) - 1, int(round(0.95 * (len(lat) - 1))))
                ],
                "real_time_factor": round(
                    sum(r["latency_s"] for r in rs) / sum(r["audio_s"] for r in rs), 3
                ),
                "cpu_cores_used": round(
                    sum(r["cpu_s"] for r in rs) / sum(r["latency_s"] for r in rs), 1
                ),
            }
        report["results"][size] = {
            "load_s": round(load_s, 1),
            "groups": groups,
            "silence_3s": {"text": s_out.text, "empty": not s_out.text.strip()},
            "rows": rows,
        }
        print(f"\n{size}: model load {load_s:.1f}s; silence -> {s_out.text!r}")
        print(
            f"  {'group':20s} {'n':>3} {'WER':>6} {'lang ok':>8} {'p50 s':>6} {'p95 s':>6} {'RTF':>6} {'cores':>6}"
        )
        for g, v in groups.items():
            print(
                f"  {g:20s} {v['n']:>3} {v['wer']:>6.1%} {v['language_correct']:>4}/{v['n']:<3} "
                f"{v['latency_p50_s']:>6.2f} {v['latency_p95_s']:>6.2f} {v['real_time_factor']:>6.2f} {v['cpu_cores_used']:>6}"
            )
    if a.out:
        a.out.parent.mkdir(parents=True, exist_ok=True)
        a.out.write_text(
            json.dumps(report, ensure_ascii=False, indent=1), encoding="utf-8"
        )
        print(f"\nwritten {a.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
