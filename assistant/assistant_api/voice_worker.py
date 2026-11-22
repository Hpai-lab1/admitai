"""LiveKit voice-agent worker (PRD F2.1, F2.2, F2.4, F2.5, 9.3).

    source local.env.sh && cd assistant && python -m assistant_api.voice_worker

Browser <-> LiveKit (WebRTC) <-> this worker. Per room (one per conversation, named by
livekit.room_name), the worker:

1. listens to the visitor's microphone track (16 kHz mono);
2. finds speech with Silero VAD (vad.py): hands-free by default, or push-to-talk when the widget
   says so (data message {"type": "mode", "mode": "ptt"} and {"type": "ptt", "down": true|false});
3. sends live captions while the visitor speaks (faster-whisper on the audio so far, about once a
   second: F2.2), and the final transcript at the end of speech;
4. answers through the same code as push-to-talk (voice_turns.spoken_answer: engine, sentence
   checks, digit-by-digit number capture), and speaks each checked sentence as soon as it is
   released, so audio starts after the first sentence (9.3);
5. barge-in (F2.4): when the visitor starts speaking while the assistant thinks or speaks, the
   queued audio is dropped at once and nothing more of that answer is said. The answer itself
   still finishes and is saved to the transcript, like a chat answer whose visitor disconnected,
   so the conversation history stays whole.

Which college, conversation and session a room serves comes from the visitor's participant
metadata, which Module B signed into the LiveKit token (POST /voice/token): the browser cannot
change it. The worker finds new rooms by asking the LiveKit server (RoomService ListRooms).

Data messages to the browser (topic "admitai", JSON): state (listening / thinking / speaking /
idle), caption (final false|true), answer (one sentence, for the captions), barge_in, done
(outcome, sources, timings), error. Not in the API contract: they travel inside LiveKit, between
this worker and the widget (see contract-proposal/CONTRACT-PROPOSAL.md).
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, AsyncIterator, Awaitable, Callable

import numpy as np

from . import vad as vadmod
from . import voice, voice_turns

log = logging.getLogger("assistant_api.voice_worker")
TOPIC = "admitai"
# PRD 10.2: 5 concurrent voice sessions in Level 1. Push-to-talk (the API process) has its own 5
# slots; set LIVEKIT_MAX_SESSIONS so that the two together stay within what the server can carry.
MAX_SESSIONS = int(os.environ.get("LIVEKIT_MAX_SESSIONS", str(voice.MAX_SESSIONS)))
CAPTION_EVERY_S = 1.0  # live caption refresh while the visitor speaks (F2.2)
POLL_S = 0.5  # how often the dispatcher looks for new rooms
IDLE_LEAVE_S = 15  # leave a room this long after the visitor has gone
FRAME_MS = 20  # audio frames pushed to LiveKit
BUSY_RETRY_S = 30  # a room turned away as busy is tried again after this long


# ----------------------------------------------------------------- the conversation (no LiveKit)


@dataclass
class Visitor:
    tenant: str
    conversation_id: str
    session_id: str | None
    record_audio: bool = False  # F2.6: consent given when the token was issued
    language: str | None = None


@dataclass
class Ports:
    """What a voice conversation needs from the outside; tests pass fakes."""

    vad: Callable[[np.ndarray], float]  # one 512-sample window -> speech probability
    transcribe: Callable[[bytes, str | None], voice.Transcript]  # wav, language -> text
    answer: Callable[
        [voice.Transcript, str], AsyncIterator[dict]
    ]  # voice_turns.spoken_answer
    synthesize: Callable[[str], tuple[bytes, int]]  # sentence -> (pcm16 mono, rate)
    # queue audio frame by frame while alive() holds; returns when queued or stopped
    play: Callable[[bytes, int, Callable[[], bool]], Awaitable[None]]
    clear: Callable[[], None]  # drop queued audio now (barge-in)
    send: Callable[[dict], Awaitable[None]]  # data message to the visitor
    keep_audio: Callable[[bytes, str], None] | None = None  # F2.6 store, when consented
    clock: Callable[[], float] = time.monotonic


@dataclass
class Conversation:
    """One visitor's voice session: VAD, captions, turns and barge-in. Transport-free."""

    visitor: Visitor
    ports: Ports
    segmenter: vadmod.Segmenter = field(default_factory=vadmod.Segmenter)
    mode: str = "hands_free"  # or "ptt"
    state: str = "idle"
    timings: list[dict] = field(default_factory=list)
    barge_ins: list[dict] = field(default_factory=list)
    _windows: vadmod.Windows = field(default_factory=vadmod.Windows)
    _turn: asyncio.Task | None = None
    _caption: asyncio.Task | None = None
    _last_caption: float = 0.0
    _ptt_down: bool = False
    _ptt_buf: list[np.ndarray] = field(default_factory=list)
    _muted_turn: int = 0  # a barged-in turn keeps running, silently
    _turn_no: int = 0
    _background: set = field(default_factory=set)

    async def set_state(self, state: str) -> None:
        if state != self.state:
            self.state = state
            await self.ports.send({"type": "state", "state": state})

    # -- input

    async def control(self, msg: dict) -> None:
        kind = msg.get("type")
        if kind == "mode" and msg.get("mode") in ("hands_free", "ptt"):
            self.mode = msg["mode"]
            self.segmenter.reset()
        elif kind == "language" and msg.get("lang") in ("en", "hi", "kn", None):
            self.visitor.language = msg.get("lang")
        elif kind == "ptt":
            down = bool(msg.get("down"))
            if down and not self._ptt_down:
                self._ptt_down, self._ptt_buf = True, []
                await self._barge_in(onset_ms=None)
                await self.set_state("listening")
            elif not down and self._ptt_down:
                self._ptt_down = False
                audio = np.concatenate(self._ptt_buf) if self._ptt_buf else np.zeros(0)
                self._ptt_buf = []
                await self._utterance(audio, self.ports.clock())

    async def audio(self, samples: np.ndarray) -> None:
        """Microphone audio, float32 16 kHz mono, any frame size."""
        for w in self._windows.push(samples):
            if self.mode == "ptt":
                if self._ptt_down:
                    self._ptt_buf.append(w)
                continue
            prob = self.ports.vad(w)
            for ev in self.segmenter.feed(w, prob):
                if ev.kind == "start":
                    await self._barge_in(onset_ms=ev.onset_ms, detected_ms=ev.at_ms)
                    await self.set_state("listening")
                elif ev.audio is not None:
                    await self._utterance(ev.audio, self.ports.clock())
                else:  # too short: a cough or a click
                    await self.set_state("idle" if self._turn is None else self.state)
            if self.segmenter.speaking:
                self._maybe_caption()

    # -- captions (F2.2)

    def _maybe_caption(self) -> None:
        now = self.ports.clock()
        if now - self._last_caption < CAPTION_EVERY_S:
            return
        if self._caption and not self._caption.done():
            return  # the previous partial is still being transcribed: never queue them up
        self._last_caption = now
        audio = self.segmenter.audio_so_far()

        async def partial() -> None:
            try:
                heard = await asyncio.to_thread(
                    self.ports.transcribe,
                    vadmod.float_to_wav(audio),
                    self.visitor.language,
                )
            except Exception:  # noqa: BLE001 - a missed partial caption is not an error
                return
            if heard.text and self.segmenter.speaking:
                await self.ports.send(
                    {"type": "caption", "final": False, "text": heard.text}
                )

        self._caption = asyncio.create_task(partial())

    # -- turns

    async def _utterance(self, audio: np.ndarray, ended_at: float) -> None:
        if len(audio) < vadmod.RATE * self.segmenter.min_speech_ms / 1000:
            await self.set_state("idle")
            return
        if self._caption and not self._caption.done():
            self._caption.cancel()
        await self.set_state("thinking")
        self._turn_no += 1
        self._turn = asyncio.create_task(self._run_turn(self._turn_no, audio, ended_at))
        self._background.add(self._turn)
        self._turn.add_done_callback(self._background.discard)

    async def _run_turn(self, n: int, audio: np.ndarray, ended_at: float) -> None:
        p = self.ports
        wav = vadmod.float_to_wav(audio)
        t_stt = p.clock()
        try:
            heard = await asyncio.to_thread(p.transcribe, wav, self.visitor.language)
        except Exception as exc:  # noqa: BLE001
            log.warning("speech-to-text failed: %s", exc.__class__.__name__)
            await p.send({"type": "error", "error": "stt"})
            await self.set_state("idle")
            return
        stt_ms = int((p.clock() - t_stt) * 1000)
        if self.visitor.language and voice_route_wrong_script(
            heard.text, self.visitor.language
        ):
            heard = voice.Transcript(heard.text, self.visitor.language, -10.0)
        lang = voice_turns.reply_language(self.visitor.language, heard)
        await p.send({"type": "caption", "final": True, "text": heard.text})
        if self.visitor.record_audio and heard.text and p.keep_audio:
            try:
                await asyncio.to_thread(p.keep_audio, wav, heard.text)
            except Exception:  # noqa: BLE001 - never lose the answer over storage
                log.warning("could not store a consented recording")
        first_audio: float | None = None
        result: dict = {}
        queue: asyncio.Queue = asyncio.Queue()

        async def speak_all() -> None:
            nonlocal first_audio
            while (item := await queue.get()) is not None:
                if self._muted_turn >= n:
                    continue
                pcm, rate = item
                if first_audio is None:
                    first_audio = p.clock()
                    await self.set_state("speaking")
                await p.play(pcm, rate, lambda: self._muted_turn < n)

        speaker = asyncio.create_task(speak_all())
        try:
            async for ev in p.answer(heard, lang):
                if "sentence" in ev and ev["sentence"].strip():
                    if self._muted_turn >= n:
                        continue  # barged in: finish silently so the transcript is whole
                    await p.send({"type": "answer", "text": ev["sentence"]})
                    if lang in voice.SPOKEN_LANGUAGES:
                        try:
                            pcm = await asyncio.to_thread(p.synthesize, ev["sentence"])
                        except Exception:  # noqa: BLE001 - PRD F2: the caption still shows
                            continue
                        queue.put_nowait(pcm)
                elif "result" in ev:
                    result = ev["result"]
        except voice_turns.NotYourConversation:
            await p.send({"type": "error", "error": "forbidden"})
        except Exception:  # noqa: BLE001
            log.exception("voice turn failed")
            await p.send({"type": "error", "error": "turn"})
        finally:
            queue.put_nowait(None)
            await speaker
        if self._muted_turn >= n:
            return
        timing = {
            "stt_ms": stt_ms,
            # PRD F2.5: end of the visitor's speech -> first audio of the answer.
            "first_audio_ms": int((first_audio - ended_at) * 1000)
            if first_audio
            else None,
            "total_ms": int((p.clock() - ended_at) * 1000),
        }
        self.timings.append(timing)
        await p.send(
            {
                "type": "done",
                "outcome": result.get("outcome"),
                "offer_handoff": result.get("offer_handoff", False),
                "sources": result.get("sources", []),
                "message_id": result.get("message_id"),
                "timings": timing,
            }
        )
        if self._turn is not None and self._turn_no == n:
            self._turn = None
            await self.set_state("idle")

    async def _barge_in(
        self, onset_ms: int | None, detected_ms: int | None = None
    ) -> None:
        """F2.4: the visitor speaks over the assistant: stop the audio within 300 ms."""
        if self._turn is None or self._turn.done():
            return
        t0 = self.ports.clock()
        self._muted_turn = self._turn_no
        self.ports.clear()
        self._turn = None
        record = {
            # From the first speech-like window to the stop: detection (VAD windows) plus the
            # worker's own reaction time.
            "stop_ms": ((detected_ms - onset_ms) if onset_ms is not None else 0)
            + int((self.ports.clock() - t0) * 1000),
        }
        self.barge_ins.append(record)
        await self.ports.send({"type": "barge_in", **record})


def voice_route_wrong_script(text: str, language: str) -> bool:
    from .routes.voice import wrong_script

    return wrong_script(text, language)


# ----------------------------------------------------------------- LiveKit transport


def _http_url(ws_url: str) -> str:
    return ws_url.replace("wss://", "https://").replace("ws://", "http://").rstrip("/")


def service_token(api_key: str, api_secret: str, minutes: int = 5) -> str:
    """A LiveKit server API token that may list rooms (RoomService), nothing else."""
    import jwt
    from datetime import datetime, timedelta, timezone

    now = datetime.now(timezone.utc)
    return jwt.encode(
        {
            "iss": api_key,
            "sub": "admitai-voice-dispatcher",
            "nbf": int(now.timestamp()),
            "exp": int((now + timedelta(minutes=minutes)).timestamp()),
            "video": {"roomList": True},
        },
        api_secret,
        algorithm="HS256",
    )


async def list_rooms(settings, client) -> list[dict]:
    r = await client.post(
        _http_url(settings.livekit_url) + "/twirp/livekit.RoomService/ListRooms",
        json={},
        headers={
            "Authorization": "Bearer "
            + service_token(settings.livekit_api_key, settings.livekit_api_secret)
        },
    )
    r.raise_for_status()
    return r.json().get("rooms", [])


def parse_visitor(metadata: str) -> Visitor | None:
    """The participant metadata Module B signed into the visitor's room token."""
    try:
        m = json.loads(metadata or "{}")
    except ValueError:
        return None
    if not isinstance(m, dict) or not m.get("tid") or not m.get("cid"):
        return None
    return Visitor(
        str(m["tid"]),
        str(m["cid"]),
        m.get("sid"),
        record_audio=m.get("aud_rec") is True,
        language=m.get("lang") if m.get("lang") in ("en", "hi", "kn") else None,
    )


class Worker:
    def __init__(self, settings, deps, experience, speech: voice.Speech, vad_path: str):
        self.settings, self.deps, self.experience, self.speech = (
            settings,
            deps,
            experience,
            speech,
        )
        self.vad_path = vad_path
        self.rooms: dict[str, asyncio.Task] = {}
        self.slots = asyncio.Semaphore(MAX_SESSIONS)
        # A room turned away as busy is not joined again at every poll.
        self.busy_until: dict[str, float] = {}

    async def run(self) -> None:
        import httpx

        async with httpx.AsyncClient(timeout=5) as client:
            while True:
                try:
                    rooms = await list_rooms(self.settings, client)
                except Exception as exc:  # noqa: BLE001 - LiveKit restarting: try again
                    log.warning("cannot list rooms: %s", exc.__class__.__name__)
                    await asyncio.sleep(2)
                    continue
                for room in rooms:
                    name = room.get("name", "")
                    if not name.startswith("admitai-") or name in self.rooms:
                        continue
                    if self.busy_until.get(name, 0) > time.monotonic():
                        continue
                    if int(room.get("num_participants", 0)) < 1:
                        continue
                    task = asyncio.create_task(self._serve(name))
                    self.rooms[name] = task
                    task.add_done_callback(lambda _t, n=name: self.rooms.pop(n, None))
                await asyncio.sleep(POLL_S)

    async def _serve(self, room_name: str) -> None:
        from livekit import rtc

        from . import livekit as lk

        busy = self.slots.locked()
        if busy:
            self.busy_until[room_name] = time.monotonic() + BUSY_RETRY_S
        async with self.slots if not busy else _nullctx():
            room = rtc.Room()
            await room.connect(
                self.settings.livekit_url,
                lk.room_token(
                    self.settings.livekit_api_key,
                    self.settings.livekit_api_secret,
                    room_name,
                    identity="assistant",
                    minutes=60,
                ),
            )
            try:
                await self._session(room, busy)
            finally:
                await room.disconnect()

    async def _session(self, room, busy: bool) -> None:
        from livekit import rtc

        loop = asyncio.get_running_loop()
        gone = asyncio.Event()
        source = rtc.AudioSource(22050, 1, queue_size_ms=500)
        track = rtc.LocalAudioTrack.create_audio_track("assistant-voice", source)
        await room.local_participant.publish_track(
            track, rtc.TrackPublishOptions(source=rtc.TrackSource.SOURCE_MICROPHONE)
        )

        async def send(msg: dict) -> None:
            await room.local_participant.publish_data(
                json.dumps(msg, ensure_ascii=False), reliable=True, topic=TOPIC
            )

        # Exactly one visitor: the one whose metadata Module B signed. Anyone else in the room
        # (a second tab, another token) is neither listened to nor obeyed.
        visitor, vid = None, None
        for p in room.remote_participants.values():
            if p.identity.startswith("visitor-") and visitor is None:
                visitor, vid = parse_visitor(p.metadata), p.identity
        if visitor is None:
            await send({"type": "error", "error": "no_visitor"})
            return
        if busy:
            await send({"type": "error", "error": "busy"})
            return
        conv = Conversation(visitor, self._ports(visitor, source, send, loop))
        await send({"type": "state", "state": "idle"})

        def on_track(track, publication, participant) -> None:
            if participant.identity == vid and track.kind == rtc.TrackKind.KIND_AUDIO:
                asyncio.ensure_future(listen(track))

        async def listen(track) -> None:
            async for ev in rtc.AudioStream(track, sample_rate=16000, num_channels=1):
                await conv.audio(vadmod.pcm16_to_float(ev.frame.data))

        def on_data(packet) -> None:
            if packet.topic != TOPIC or not packet.participant:
                return
            if packet.participant.identity != vid:
                return
            try:
                msg = json.loads(bytes(packet.data).decode())
            except ValueError:
                return
            if isinstance(msg, dict):
                asyncio.ensure_future(conv.control(msg))

        def on_left(participant) -> None:
            if participant.identity == vid:
                gone.set()

        room.on("track_subscribed", on_track)
        room.on("data_received", on_data)
        room.on("participant_disconnected", on_left)
        for p in room.remote_participants.values():
            for pub in p.track_publications.values():
                if pub.track is not None:
                    on_track(pub.track, pub, p)
        await gone.wait()
        await asyncio.sleep(0)  # let a running answer notice; it still saves
        for t in list(conv._background):
            try:
                await asyncio.wait_for(t, IDLE_LEAVE_S)
            except Exception:  # noqa: BLE001
                pass

    def _ports(self, visitor: Visitor, source, send, loop) -> Ports:
        from livekit import rtc

        from .routes.voice import keep_audio

        vad_model = vadmod.SileroVAD(self.vad_path)
        vocab_box: dict[str, str] = {}

        def transcribe(wav: bytes, language: str | None) -> voice.Transcript:
            if "v" not in vocab_box:
                vocab_box["v"] = asyncio.run_coroutine_threadsafe(
                    voice_turns.vocabulary(self.deps, visitor.tenant), loop
                ).result(10)
            return self.speech.transcribe(wav, vocab_box["v"], language)

        def answer(heard: voice.Transcript, lang: str):
            who = voice_turns.VoiceVisitor(
                visitor.tenant, visitor.conversation_id, visitor.session_id
            )
            return voice_turns.spoken_answer(
                self.deps, self.experience, who, heard, lang
            )

        def synthesize(text: str) -> tuple[bytes, int]:
            return self.speech.speak_pcm(text)

        async def play(pcm: bytes, rate: int, alive) -> None:
            n = rate * FRAME_MS // 1000
            for i in range(0, len(pcm) // 2, n):
                chunk = pcm[i * 2 : (i + n) * 2]
                samples = len(chunk) // 2
                if samples == 0 or not alive():  # barge-in: not one more frame
                    break
                await source.capture_frame(rtc.AudioFrame(chunk, rate, 1, samples))

        def store(wav: bytes, transcript: str) -> None:
            keep_audio(
                self.deps,
                visitor.tenant,
                visitor.conversation_id,
                visitor.session_id,
                "livekit",
                wav,
                transcript,
            )

        return Ports(
            vad=vad_model,
            transcribe=transcribe,
            answer=answer,
            synthesize=synthesize,
            play=play,
            clear=source.clear_queue,
            send=send,
            keep_audio=store,
        )


class _nullctx:
    async def __aenter__(self) -> None:
        return None

    async def __aexit__(self, *exc: Any) -> None:
        return None


def build(settings=None):
    from .clients import make_llm
    from .config import Settings, check_production
    from .engine import Deps
    from .experience_client import ExperienceClient
    from .guard import RateLimiter
    from .knowledge import KnowledgeClient

    settings = settings or Settings()
    check_production(settings)
    if not (
        settings.livekit_url
        and settings.livekit_api_key
        and settings.livekit_api_secret
    ):
        raise SystemExit("Set LIVEKIT_URL, LIVEKIT_API_KEY and LIVEKIT_API_SECRET")
    deps = Deps(
        settings,
        KnowledgeClient(settings),
        make_llm(settings),
        RateLimiter(settings.rate_limit_per_minute, 60),
    )
    speech = voice.Speech(
        settings.voice_model_dir,
        settings.whisper_size,
        settings.piper_voice,
        whisper_size_indic=settings.whisper_size_indic,
        stt_workers=2,  # live captions and the final transcript in parallel
    )
    vad_path = os.environ.get("VAD_MODEL") or os.path.join(
        settings.voice_model_dir, "vad", "silero_vad.onnx"
    )
    return Worker(settings, deps, ExperienceClient(settings), speech, vad_path)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--no-preload", action="store_true")
    ap.add_argument(
        "--pid-file", type=Path, help="write the process id here (stop scripts)"
    )
    a = ap.parse_args()
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s"
    )
    logging.getLogger("httpx").setLevel(logging.WARNING)  # not one line per room poll
    worker = build()
    if a.pid_file:
        a.pid_file.write_text(str(os.getpid()))
    vadmod.SileroVAD(worker.vad_path)  # fail now, not on the first visitor
    if not a.no_preload:
        worker.speech.preload()
    log.info("voice worker ready (LiveKit %s)", worker.settings.livekit_url)
    asyncio.run(worker.run())


if __name__ == "__main__":
    main()
