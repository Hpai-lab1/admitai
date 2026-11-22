"""Retell AI custom-LLM WebSocket (retell.py): Retell sends what the visitor said; every answer
comes from the engine via voice_turns.spoken_answer, streamed back sentence by sentence.

Retell may ask for an answer before the visitor has finished ("What is the application" ...
"fee for the course?"), then ask again with a newer response_id. Only the newest request is
answered: an older answer still being prepared is cancelled, so the visitor never waits behind a
half-heard question. A slow answer starts with a short "One moment" so the call is never silent.
"""

from __future__ import annotations

import asyncio
import json
import logging

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from .. import language, retell, voice, voice_turns

log = logging.getLogger(__name__)
router = APIRouter(tags=["voice"])

GREETING = {
    "en": "Hello! Ask me anything about admissions.",
    "hi": "नमस्ते! प्रवेश के बारे में कुछ भी पूछिए।",
    "kn": "ನಮಸ್ಕಾರ! ಪ್ರವೇಶದ ಬಗ್ಗೆ ಏನು ಬೇಕಾದರೂ ಕೇಳಿ.",
}
HOLD_ON = {
    "en": "One moment, let me check that.",
    "hi": "एक पल, मैं देखता हूँ।",
    "kn": "ಒಂದು ಕ್ಷಣ, ನಾನು ನೋಡುತ್ತೇನೆ.",
}
HOLD_AFTER_S = 1.2  # say "one moment" only when the answer is not ready by then


@router.websocket("/voice/retell/llm/{call_id}")
async def retell_llm(ws: WebSocket, call_id: str):
    await ws.accept()
    deps_ = getattr(ws.app.state, "deps", None)
    call = await asyncio.to_thread(
        retell.lookup, call_id, deps_.settings.database_url if deps_ else None
    )
    if call is None:  # not a call this service created: answer nothing
        await ws.close(code=1008)
        return
    deps = ws.app.state.deps
    experience = ws.app.state.experience
    lang = call.language or "en"  # the visitor's pick when the call started
    # The language of the last reply: a call can switch between English, Hindi and Kannada at
    # any turn ("Kannada please", or simply speaking Hindi); the engine decides each reply's
    # language (language.decide) and Retell's multilingual voice speaks whatever it is sent.
    spoken_lang = {"now": lang}
    who = voice_turns.VoiceVisitor(call.tenant, call.conversation_id, call.session_id)
    send_lock = asyncio.Lock()

    async def send(payload: dict) -> None:
        async with send_lock:
            await ws.send_json(payload)

    async def answer(rid: int, text: str) -> None:
        d = language.detect(text)
        said = d.language
        # "One moment" in the language asked for ("English please"), else the one being spoken
        # now (only when most of the sentence is in that script, as language.decide on calls),
        # else the conversation's.
        clear = d.script != "mixed" and d.share >= language.VOICE_SWITCH_SHARE
        now = (
            language.preferences(text).language
            or (said if said in ("hi", "kn") and clear else None)
            or spoken_lang["now"]
        )
        heard = voice.Transcript(text, said, 0.0 if text else -10.0, "retell")
        first = asyncio.Event()

        async def hold_on() -> None:
            try:
                await asyncio.wait_for(first.wait(), HOLD_AFTER_S)
            except asyncio.TimeoutError:
                await send(
                    retell.response(rid, HOLD_ON.get(now, HOLD_ON["en"]) + " ", False)
                )

        holder = asyncio.create_task(hold_on())
        try:
            async for ev in voice_turns.spoken_answer(
                deps, experience, who, heard, now, channel="web_voice", chosen=lang
            ):
                if "result" in ev:
                    spoken_lang["now"] = ev["result"].get("language") or now
                spoken = retell.speakable(ev.get("sentence") or "")
                if spoken:
                    first.set()
                    await holder  # "one moment" (if any) goes out before the answer
                    await send(retell.response(rid, spoken, False))
            await send(retell.response(rid, "", True))
        except voice_turns.NotYourConversation:
            await send(retell.response(rid, "", True, end_call=True))
        finally:
            holder.cancel()

    await send({"response_type": "config", "config": {"auto_reconnect": True}})
    await send(retell.response(0, GREETING.get(lang, GREETING["en"]), True))
    current: asyncio.Task | None = None
    try:
        while True:
            msg = json.loads(await ws.receive_text())
            kind = msg.get("interaction_type")
            if kind == "ping_pong":
                await send(
                    {"response_type": "ping_pong", "timestamp": msg.get("timestamp")}
                )
                continue
            if kind not in ("response_required", "reminder_required"):
                continue  # update_only, call_details: nothing to say
            if current and not current.done():
                current.cancel()  # a newer request replaces the half-heard one
            current = asyncio.create_task(
                answer(
                    msg.get("response_id", 0),
                    retell.last_user_text(msg.get("transcript") or []),
                )
            )
    except WebSocketDisconnect:
        pass
    except Exception:  # noqa: BLE001  (one bad turn must not leave the caller in silence)
        log.exception("retell: call %s failed", call_id)
        try:
            await send(
                retell.response(0, voice.msg("not_heard", spoken_lang["now"]), True)
            )
        except Exception:  # noqa: BLE001
            pass
    finally:
        if current and not current.done():
            current.cancel()
