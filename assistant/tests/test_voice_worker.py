"""LiveKit voice worker (PRD F2.1, F2.2, F2.4, F2.5, 9.3) without LiveKit or models: the
conversation logic gets fake ports (VAD by loudness, scripted speech-to-text and answers) and a
clock that advances with the audio fed, so captions and timings are deterministic."""

import asyncio

import numpy as np
import pytest

from assistant_api import vad, voice
from assistant_api.voice_worker import Conversation, Ports, Visitor, parse_visitor

RATE = vad.RATE


def tone(seconds: float) -> np.ndarray:
    return (0.3 * np.ones(int(RATE * seconds))).astype(np.float32)


def silence(seconds: float) -> np.ndarray:
    return np.zeros(int(RATE * seconds), np.float32)


class Rig:
    """Fake ports around one Conversation; the clock is the audio time fed so far."""

    def __init__(self, sentences=("First sentence [1].", "Second sentence."), **kw):
        self.t = 0.0
        self.sent: list[dict] = []
        self.played: list[bytes] = []
        self.cleared = 0
        self.kept: list[str] = []
        self.heard = kw.get("heard", "What is the fee?")
        self.sentences = list(sentences)
        self.gate: asyncio.Event | None = kw.get("gate")
        self.answered = asyncio.Event()
        self.stt_calls = 0

        async def answer(heard, lang):
            for i, s in enumerate(self.sentences):
                if i == 1 and self.gate is not None:
                    await self.gate.wait()  # the second sentence waits for the test
                yield {"sentence": s}
            self.answered.set()
            yield {"result": {"outcome": "answered", "sources": [], "message_id": "m1"}}

        def transcribe(wav, language):
            self.stt_calls += 1
            return voice.Transcript(self.heard, "en", -0.2)

        async def play(pcm, rate, alive):
            if alive():
                self.played.append(pcm)

        async def send(msg):
            self.sent.append(msg)

        def clear():
            self.cleared += 1

        self.ports = Ports(
            vad=lambda w: 0.9 if float(np.abs(w).max()) > 0.1 else 0.0,
            transcribe=transcribe,
            answer=answer,
            synthesize=lambda text: (text.encode(), 22050),
            play=play,
            clear=clear,
            send=send,
            keep_audio=lambda wav, text: self.kept.append(text),
            clock=lambda: self.t,
        )
        self.conv = Conversation(
            Visitor("t1", "c1", "s1", record_audio=kw.get("record", False)),
            self.ports,
        )

    async def feed(self, audio: np.ndarray, chunk_s: float = 0.02) -> None:
        n = int(RATE * chunk_s)
        for i in range(0, len(audio), n):
            part = audio[i : i + n]
            self.t += len(part) / RATE
            await self.conv.audio(part)
            await asyncio.sleep(0)

    def kinds(self, kind):
        return [m for m in self.sent if m["type"] == kind]

    async def settle(self):
        for _ in range(50):
            await asyncio.sleep(0)
        for t in list(self.conv._background):
            await asyncio.wait_for(t, 2)


def run(coro):
    return asyncio.run(coro)


def test_hands_free_turn_captions_answers_and_speaks_each_sentence_in_order():
    async def go():
        r = Rig()
        await r.feed(silence(0.5))
        await r.feed(tone(1.2))
        await r.feed(silence(0.5))
        await r.settle()
        return r

    r = run(go())
    states = [m["state"] for m in r.kinds("state")]
    assert states[:3] == ["listening", "thinking", "speaking"] and states[-1] == "idle"
    assert r.kinds("caption")[-1] == {
        "type": "caption",
        "final": True,
        "text": "What is the fee?",
    }
    assert [m["text"] for m in r.kinds("answer")] == [
        "First sentence [1].",
        "Second sentence.",
    ]
    assert r.played == [b"First sentence [1].", b"Second sentence."]  # in order
    done = r.kinds("done")[0]
    assert done["outcome"] == "answered" and done["message_id"] == "m1"
    assert set(done["timings"]) == {"stt_ms", "first_audio_ms", "total_ms"}


async def wait_until(check, timeout=2.0):
    """Wait for the worker instead of a fixed number of event-loop turns: on a busy CI runner 50
    turns were sometimes not enough, and the test failed although the code worked."""
    loop = asyncio.get_running_loop()
    end = loop.time() + timeout
    while not check() and loop.time() < end:
        await asyncio.sleep(0.005)


def test_audio_starts_after_the_first_sentence_not_the_whole_answer():
    async def go():
        gate = asyncio.Event()
        r = Rig(gate=gate)
        await r.feed(tone(1.0))
        await r.feed(silence(0.4))
        await wait_until(lambda: r.played)
        first = list(r.played)
        gate.set()
        await r.settle()
        return r, first

    r, first = run(go())
    assert first == [
        b"First sentence [1]."
    ]  # spoken while the second was not yet written
    assert len(r.played) == 2


def test_barge_in_stops_the_audio_within_300_ms_and_drops_the_rest_of_the_answer():
    async def go():
        gate = asyncio.Event()
        r = Rig(gate=gate)
        await r.feed(tone(1.0))
        await r.feed(silence(0.4))
        await wait_until(lambda: r.conv.state == "speaking")
        assert r.conv.state == "speaking"
        await r.feed(tone(0.2))  # the visitor talks over the assistant
        gate.set()
        await r.settle()
        return r

    r = run(go())
    assert r.cleared == 1
    (b,) = r.kinds("barge_in")
    assert b["stop_ms"] <= 300  # F2.4
    assert r.played == [b"First sentence [1]."]  # the second sentence is never spoken
    assert [m["text"] for m in r.kinds("answer")] == ["First sentence [1]."]
    assert r.answered.is_set()  # the answer still finished (and is saved)
    assert not r.kinds("done")  # but is not announced as this turn's answer


def test_a_cough_does_not_interrupt_or_start_a_turn():
    async def go():
        r = Rig()
        await r.feed(tone(0.06))  # 60 ms: under the 96 ms start threshold
        await r.feed(silence(0.5))
        await r.settle()
        return r

    r = run(go())
    assert r.stt_calls == 0 and not r.kinds("answer") and r.cleared == 0


def test_push_to_talk_uses_the_button_not_the_voice_detector():
    async def go():
        r = Rig()
        await r.conv.control({"type": "mode", "mode": "ptt"})
        await r.feed(tone(1.0))  # speech without the button: ignored
        await r.feed(silence(0.5))
        await r.settle()
        assert r.stt_calls == 0
        await r.conv.control({"type": "ptt", "down": True})
        await r.feed(tone(0.8))
        await r.conv.control({"type": "ptt", "down": False})
        await r.settle()
        return r

    r = run(go())
    assert r.stt_calls >= 1 and len(r.played) == 2


def test_live_captions_while_speaking_then_the_final_one():
    async def go():
        r = Rig()
        await r.feed(tone(3.0))
        await asyncio.sleep(0.05)  # let the partial transcription threads finish
        await r.feed(silence(0.4))
        await r.settle()
        return r

    r = run(go())
    caps = r.kinds("caption")
    assert any(not c["final"] for c in caps)  # F2.2: live, while speaking
    assert caps[-1]["final"] is True


@pytest.mark.parametrize("record", [True, False])
def test_recordings_are_kept_only_with_consent(record):
    async def go():
        r = Rig(record=record)
        await r.feed(tone(1.0))
        await r.feed(silence(0.4))
        await r.settle()
        return r

    r = run(go())
    assert r.kept == (["What is the fee?"] if record else [])


def test_hindi_and_kannada_answers_are_captions_only_without_a_voice():
    async def go():
        r = Rig()
        r.conv.visitor.language = "kn"
        r.heard = "ಶುಲ್ಕ ಎಷ್ಟು?"
        await r.feed(tone(1.0))
        await r.feed(silence(0.4))
        await r.settle()
        return r

    r = run(go())
    assert r.kinds("answer") and r.played == []  # no Kannada Piper voice: text only


def test_room_metadata_must_name_tenant_and_conversation():
    v = parse_visitor('{"tid": "t", "cid": "c", "sid": "s", "aud_rec": true}')
    assert v.tenant == "t" and v.conversation_id == "c" and v.record_audio
    assert parse_visitor('{"tid": "t"}') is None
    assert parse_visitor("not json") is None
    assert parse_visitor('{"tid":"t","cid":"c","aud_rec":"yes"}').record_audio is False


# ------------------------------------------------------------------ the segmenter itself


def feed_probs(seg, probs):
    out = []
    for p in probs:
        out += seg.feed(np.full(vad.WINDOW, p, np.float32), p)
    return out


def test_segmenter_starts_after_three_windows_and_ends_after_300_ms_of_silence():
    seg = vad.Segmenter()
    evs = feed_probs(seg, [0.0] * 5 + [0.9] * 2)
    assert evs == []  # 64 ms of speech: not yet
    evs = feed_probs(seg, [0.9])
    assert [e.kind for e in evs] == ["start"] and evs[0].at_ms - evs[0].onset_ms == 96
    evs = feed_probs(seg, [0.9] * 10 + [0.0] * 9)  # 288 ms of silence: still speaking
    assert evs == [] and seg.speaking
    (end,) = feed_probs(seg, [0.0])
    assert end.kind == "end" and end.audio is not None
    # pre-roll: the windows just before the start are part of the utterance
    assert len(end.audio) >= vad.WINDOW * (3 + 10 + 10)


def test_segmenter_cuts_very_long_speech_and_drops_very_short_bursts():
    seg = vad.Segmenter(max_utterance_ms=1000)
    evs = feed_probs(seg, [0.9] * 40)
    # cut after 1 s; speech that goes on starts the next utterance
    assert [e.kind for e in evs][:2] == ["start", "end"] and evs[1].audio is not None
    seg = vad.Segmenter()
    evs = feed_probs(seg, [0.9] * 4 + [0.0] * 10)
    assert [e.kind for e in evs] == ["start", "end"] and evs[1].audio is None


def test_windows_rechunks_any_frame_size():
    w = vad.Windows()
    assert w.push(np.zeros(300, np.float32)) == []
    out = w.push(np.zeros(800, np.float32))
    assert len(out) == 2 and all(len(x) == vad.WINDOW for x in out)


def test_dispatcher_joins_new_admitai_rooms_once_and_waits_after_busy(monkeypatch):
    from assistant_api import voice_worker as vw

    rooms = [
        {"name": "admitai-aaa", "num_participants": 1},
        {"name": "admitai-empty", "num_participants": 0},
        {"name": "someone-else", "num_participants": 2},
    ]
    served: list[str] = []
    polls = {"n": 0}

    async def fake_list(settings, client):
        polls["n"] += 1
        if polls["n"] > 4:
            raise asyncio.CancelledError
        return rooms

    w = vw.Worker(None, None, None, None, "vad.onnx")

    async def fake_serve(name):
        served.append(name)
        await asyncio.sleep(10)  # still serving at the next polls

    monkeypatch.setattr(vw, "list_rooms", fake_list)
    monkeypatch.setattr(vw, "POLL_S", 0)
    monkeypatch.setattr(w, "_serve", fake_serve)
    w.busy_until["admitai-busy"] = float("inf")
    rooms.append({"name": "admitai-busy", "num_participants": 1})
    with pytest.raises(asyncio.CancelledError):
        asyncio.run(w.run())
    assert served == ["admitai-aaa"]  # once, although polled four times


def test_a_long_programme_list_is_left_out_of_the_speech_hint():
    """Measured: a 213-programme list, cut to fit, made Whisper write programme names it was
    never told; a short list still goes in whole."""
    from assistant_api import voice_turns

    class K:
        def __init__(self, n):
            self.n = n

        async def programs(self, tenant):
            return {
                "items": [
                    {"name": f"B.Tech Programme {i}", "aliases": []}
                    for i in range(self.n)
                ]
            }

    class D:
        def __init__(self, n):
            self.knowledge = K(n)

    short = asyncio.run(voice_turns.vocabulary(D(3), "t"))
    assert "B.Tech Programme 2" in short
    assert asyncio.run(voice_turns.vocabulary(D(213), "t")) == ""
