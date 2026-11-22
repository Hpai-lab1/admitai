"""Voice-activity detection for the LiveKit voice worker (PRD F2.1 hands-free mode, 7.5 "End-of-speech
detection (VAD) 300 ms: Silero VAD silence threshold", F2.4 barge-in within 300 ms).

Two parts, so the decisions can be tested without the model:

- `SileroVAD`: the Silero VAD v5 ONNX model on the CPU (onnxruntime; about 0.1 ms per 32 ms window).
  It turns one 512-sample window of 16 kHz audio into the probability that someone is speaking.
- `Segmenter`: a small state machine over those probabilities. Speech starts after
  `start_ms` of speech-like windows (short enough for barge-in, long enough that a cough or a
  click does not interrupt the assistant), and ends after `end_ms` of silence (the PRD's 300 ms).

The model file is VAD_MODEL (default VOICE_MODEL_DIR/vad/silero_vad.onnx, MIT licence, from
github.com/snakers4/silero-vad v5.1.2).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

RATE = 16_000
WINDOW = 512  # samples per Silero window at 16 kHz (32 ms)
WINDOW_MS = WINDOW * 1000 // RATE
_CONTEXT = 64  # Silero v5 sees the last 64 samples of the previous window as well


class SileroVAD:
    def __init__(self, path: str | Path):
        import onnxruntime as ort

        path = Path(path)
        if not path.exists():
            raise RuntimeError(f"Silero VAD model not found: {path}")
        opts = ort.SessionOptions()
        opts.intra_op_num_threads = 1  # one window is tiny; threads only add overhead
        opts.inter_op_num_threads = 1
        self._s = ort.InferenceSession(
            str(path), sess_options=opts, providers=["CPUExecutionProvider"]
        )
        self.reset()

    def reset(self) -> None:
        self._state = np.zeros((2, 1, 128), dtype=np.float32)
        self._context = np.zeros((1, _CONTEXT), dtype=np.float32)

    def __call__(self, window: np.ndarray) -> float:
        """One window of WINDOW float32 samples in [-1, 1] -> speech probability."""
        x = np.concatenate([self._context, window.reshape(1, WINDOW)], axis=1)
        out, self._state = self._s.run(
            None,
            {"input": x, "state": self._state, "sr": np.array(RATE, dtype=np.int64)},
        )
        self._context = x[:, -_CONTEXT:]
        return float(out[0][0])


@dataclass
class Event:
    kind: str  # "start" | "end"
    audio: np.ndarray | None = None  # for "end": the utterance, float32 at 16 kHz
    at_ms: int = 0  # stream time of the event (ms of audio fed so far)
    onset_ms: int = 0  # for "start": when the first speech-like window began


@dataclass
class Segmenter:
    """Probabilities in, "start"/"end" events out. Keeps `pre_ms` of audio from before the start,
    so the first syllable is not cut off."""

    threshold: float = 0.5
    neg_threshold: float = (
        0.35  # hysteresis: speech stays "on" until it is clearly gone
    )
    start_ms: int = 96  # 3 windows of speech before "start" (barge-in budget 300 ms)
    end_ms: int = 300  # PRD 7.5: end of speech after 300 ms of silence
    pre_ms: int = 200
    min_speech_ms: int = 250  # shorter than this is a noise, not a question
    max_utterance_ms: int = 20_000  # a question, not a lecture: cut and answer
    speaking: bool = False
    _run_ms: int = 0
    _silence_ms: int = 0
    _speech_ms: int = 0
    _t_ms: int = 0
    _onset_ms: int = 0
    _pre: list[np.ndarray] = field(default_factory=list)
    _buf: list[np.ndarray] = field(default_factory=list)

    def reset(self) -> None:
        self.speaking = False
        self._run_ms = self._silence_ms = self._speech_ms = 0
        self._pre, self._buf = [], []

    def audio_so_far(self) -> np.ndarray:
        """The utterance being spoken now (for live captions)."""
        return np.concatenate(self._buf) if self._buf else np.zeros(0, np.float32)

    def feed(self, window: np.ndarray, prob: float) -> list[Event]:
        self._t_ms += WINDOW_MS
        events: list[Event] = []
        if not self.speaking:
            self._pre.append(window)
            keep = max(1, self.pre_ms // WINDOW_MS)
            self._pre = self._pre[-keep:]
            if prob >= self.threshold:
                if self._run_ms == 0:
                    self._onset_ms = self._t_ms - WINDOW_MS
                self._run_ms += WINDOW_MS
            else:
                self._run_ms = 0
            if self._run_ms >= self.start_ms:
                self.speaking = True
                self._buf = list(self._pre)
                self._pre = []
                self._speech_ms = self._run_ms
                self._silence_ms = 0
                events.append(Event("start", at_ms=self._t_ms, onset_ms=self._onset_ms))
            return events
        self._buf.append(window)
        if prob < self.neg_threshold:
            self._silence_ms += WINDOW_MS
        else:
            self._silence_ms = 0
            self._speech_ms += WINDOW_MS
        too_long = len(self._buf) * WINDOW_MS >= self.max_utterance_ms
        if self._silence_ms >= self.end_ms or too_long:
            audio = np.concatenate(self._buf)
            long_enough = self._speech_ms >= self.min_speech_ms
            self.reset()
            self._run_ms = 0
            events.append(
                Event("end", audio=audio if long_enough else None, at_ms=self._t_ms)
            )
        return events


class Windows:
    """Re-chunks audio of any frame size into exact WINDOW-sample windows."""

    def __init__(self) -> None:
        self._rest = np.zeros(0, np.float32)

    def push(self, samples: np.ndarray) -> list[np.ndarray]:
        x = np.concatenate([self._rest, samples.astype(np.float32)])
        n = len(x) // WINDOW
        self._rest = x[n * WINDOW :]
        return [x[i * WINDOW : (i + 1) * WINDOW] for i in range(n)]


def pcm16_to_float(data: bytes | memoryview) -> np.ndarray:
    return np.frombuffer(bytes(data), dtype=np.int16).astype(np.float32) / 32768.0


def float_to_wav(audio: np.ndarray, rate: int = RATE) -> bytes:
    import io
    import wave

    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes((np.clip(audio, -1, 1) * 32767).astype(np.int16).tobytes())
    return buf.getvalue()
