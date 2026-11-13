"""Voice API (PRD F2, 12.1 POST /voice/token): push-to-talk turns and spoken answers."""

from __future__ import annotations

import asyncio
import base64
import re
import time
from datetime import datetime, timedelta, timezone

import jwt
from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import Response
from pydantic import BaseModel, Field

from .. import livekit, spoken_lead, store, voice
from ..auth import Caller, caller, service_only, visitor_limits
from ..clients import UpstreamError
from ..engine import Deps, Forbidden, handle_turn
from ..experience_client import ExperienceError
from .chat import deps_dep

router = APIRouter(tags=["voice"])
TOKEN_MINUTES = 10
MAX_AUDIO_BYTES = 5 * 1024 * 1024  # about a minute of compressed speech


def speech_dep(request: Request) -> voice.Speech:
    return request.app.state.speech


class TokenIn(BaseModel):
    conversation_id: str = Field(min_length=1, max_length=100)


@router.post(
    "/voice/token", summary="Short-lived voice token for this visitor and conversation"
)
def voice_token(
    body: TokenIn, who: Caller = Depends(caller), deps: Deps = Depends(deps_dep)
):
    now = datetime.now(timezone.utc)
    token = jwt.encode(
        {
            "typ": "voice",
            "tid": who.tenant_id,
            "sid": who.session_id,
            "cid": body.conversation_id,
            "iat": now,
            "exp": now + timedelta(minutes=TOKEN_MINUTES),
        },
        deps.settings.voice_token_secret,
        algorithm="HS256",
    )
    out = {"token": token, "expires_in": TOKEN_MINUTES * 60}
    s = deps.settings
    if s.livekit_url and s.livekit_api_key and s.livekit_api_secret:
        # PRD F2.1 / 12.1: a short-lived LiveKit room token for this visitor and conversation.
        # The push-to-talk token above stays, so a widget without WebRTC keeps working.
        room = livekit.room_name(who.tenant_id, body.conversation_id)
        out["livekit"] = {
            "url": s.livekit_url,
            "room": room,
            "token": livekit.room_token(
                s.livekit_api_key,
                s.livekit_api_secret,
                room,
                identity=f"visitor-{who.session_id or 'service'}",
                metadata={
                    "tid": who.tenant_id,
                    "cid": body.conversation_id,
                    "sid": who.session_id,
                },
                minutes=TOKEN_MINUTES,
            ),
        }
    return out


class VoiceEvalIn(BaseModel):
    chat_accuracy: float = Field(ge=0, le=1)
    voice_accuracy: float = Field(ge=0, le=1)
    cases: int = Field(ge=1)
    run_id: str | None = Field(default=None, max_length=100)


@router.post(
    "/voice/eval-result",
    summary="Module A's test-set result for voice vs chat; more than 3 points worse moves voice "
    "to the answer model (PRD F2.10)",
)
def voice_eval_result(
    body: VoiceEvalIn,
    who: Caller = Depends(service_only),
    deps: Deps = Depends(deps_dep),
):
    from ..engine import voice_decision

    decision = {
        **voice_decision(body.chat_accuracy, body.voice_accuracy),
        "chat_accuracy": body.chat_accuracy,
        "voice_accuracy": body.voice_accuracy,
        "cases": body.cases,
        "run_id": body.run_id,
        "decided_at": datetime.now(timezone.utc).isoformat(),
    }
    store.set_managed(
        deps.settings.database_url, who.tenant_id, "voice_quality", decision
    )
    return decision


def _voice_claims(token: str, secret: str) -> dict:
    try:
        claims = jwt.decode(token, secret, algorithms=["HS256"])
    except jwt.PyJWTError:
        claims = None
    if not claims or claims.get("typ") != "voice":
        raise HTTPException(401, "Voice session expired; press the microphone again")
    return claims


_SCRIPTS = {"kn": re.compile(r"[ಀ-೿]"), "hi": re.compile(r"[ऀ-ॿ]")}


def _wrong_script(text: str, language: str) -> bool:
    """Heard in a script the chosen language does not use: Kannada heard as Urdu, or an
    English-mode visitor heard as Kannada. Latin letters are fine in every language
    (programme names, or Kannada and Hindi written the English way)."""
    letters = [c for c in text if c.isalpha() and not c.isascii()]
    if not letters:
        return False
    if language == "en":
        return True
    return not _SCRIPTS[language].search(text)


@router.post(
    "/voice/turn",
    summary="One spoken question: transcript, checked answer and spoken audio",
)
async def voice_turn(
    request: Request,
    audio: UploadFile = File(...),
    token: str = Form(...),
    language: str | None = Form(None),
    expect: str | None = Form(
        None,
        description='"rating": the widget asked for a spoken 1-5 rating of the chat, so the '
        "number is read and nothing is answered",
    ),
    deps: Deps = Depends(deps_dep),
    speech: voice.Speech = Depends(speech_dep),
):
    claims = _voice_claims(token, deps.settings.voice_token_secret)
    tenant, conv = claims["tid"], claims["cid"]
    visitor_limits(request, tenant)
    data = await audio.read(MAX_AUDIO_BYTES + 1)
    if not data or len(data) > MAX_AUDIO_BYTES:
        raise HTTPException(413 if data else 422, "The recording is empty or too long")
    if (
        speech.slots.locked()
    ):  # all voice slots in use (PRD 10.2: 5 concurrent sessions)
        raise HTTPException(503, voice.msg("busy", language or "en"))
    async with speech.slots:
        t0 = time.monotonic()
        try:
            catalogue = await deps.knowledge.programs(tenant)
        except UpstreamError:
            catalogue = {"items": []}
        vocab = " ".join(
            f"{p['name']} {' '.join(p.get('aliases', []))}" for p in catalogue["items"]
        )
        try:
            # The language the visitor chose (EN, हिं, ಕ) is what the speech model listens for,
            # English too: left to guess, it once heard an English-mode visitor as Kannada.
            hint = language if language in ("en", "kn", "hi") else None
            heard = await asyncio.to_thread(speech.transcribe, data, vocab, hint)
            # Told "Kannada", a small speech model can still write the words in another script
            # (Urdu was seen). That is a mishearing, not a question: ask again, never answer it.
            if hint and _wrong_script(heard.text, hint):
                heard = voice.Transcript(heard.text, hint, -10.0)
        except RuntimeError as exc:
            raise HTTPException(503, f"Voice is not available: {exc}") from None
        stt_ms = int((time.monotonic() - t0) * 1000)
        # The chosen language decides the reply; a guess only when nothing was chosen.
        lang = (
            language
            if language in ("en", "hi", "kn")
            else (heard.language if heard.language in ("hi", "kn") else "en")
        )
        conv_row = await asyncio.to_thread(
            store.open_conversation,
            deps.settings.database_url,
            tenant,
            conv,
            claims.get("sid"),
            "web_voice",
        )
        if conv_row is None:
            raise HTTPException(403, "This conversation belongs to another session")
        state = dict(conv_row["state"] or {})
        if not heard.text or heard.confidence < voice.MIN_CONFIDENCE:
            misses = state.get("voice_misses", 0) + 1
            await asyncio.to_thread(
                store.update_state,
                deps.settings.database_url,
                tenant,
                conv,
                {"voice_misses": misses},
            )
            key = "offer_chat" if misses >= 2 else "not_heard"
            text = voice.msg(key, lang)
            return await _reply(
                speech,
                {
                    "transcript": heard.text,
                    "language": lang,
                    "answer": text,
                    "outcome": "not_heard",
                    "offer_handoff": misses >= 2,
                    "sources": [],
                },
                {"stt_ms": stt_ms},
                t0,
            )
        await asyncio.to_thread(
            store.update_state,
            deps.settings.database_url,
            tenant,
            conv,
            {"voice_misses": 0},
        )
        # Spoken end-of-chat rating: read the number, answer nothing, never call the model.
        if expect == "rating":
            n = spoken_lead.spoken_rating(heard.text)
            words = spoken_lead.RATING_TEXT.get(lang, spoken_lead.RATING_TEXT["en"])
            return await _reply(
                speech,
                {
                    "transcript": heard.text,
                    "language": lang,
                    "answer": words["thanks"].format(n=n) if n else words["unclear"],
                    "outcome": "rating" if n else "rating_unclear",
                    "rating": n,
                    "offer_handoff": False,
                    "sources": [],
                },
                {"stt_ms": stt_ms},
                t0,
            )
        # F2.9: a call-back request and the number, read back digit by digit, before the engine.
        # These turns are not written to the transcript: the number is personal data.
        reply, action = spoken_lead.step(state, heard.text, lang)
        outcome = "lead_capture"
        if action == "save":
            reply, outcome = await _save_spoken_lead(
                request.app.state.experience,
                tenant,
                conv,
                claims.get("sid"),
                state,
                lang,
            )
        if reply is not None:
            await asyncio.to_thread(
                store.update_state,
                deps.settings.database_url,
                tenant,
                conv,
                {"voice_lead": state.get("voice_lead")},
            )
            return await _reply(
                speech,
                {
                    "transcript": heard.text,
                    "language": lang,
                    "answer": reply,
                    "outcome": outcome,
                    "offer_handoff": False,
                    "sources": [],
                },
                {"stt_ms": stt_ms},
                t0,
            )
        who = Caller(tenant, claims.get("sid"), "visitor")
        answer, done, events = "", {}, {}
        t1 = time.monotonic()
        try:
            async for ev in handle_turn(deps, who, conv, heard.text, lang, "web_voice"):
                if ev["event"] == "token":
                    answer += ev["data"]["text"]
                elif ev["event"] == "done":
                    done = ev["data"]
                else:
                    events[ev["event"]] = ev["data"]
        except Forbidden as exc:
            raise HTTPException(403, str(exc)) from None
        result = {
            "transcript": heard.text,
            "language": lang,
            "answer": answer.strip(),
            "outcome": done.get("outcome"),
            "offer_handoff": done.get("offer_handoff", False),
            "sources": done.get("sources", []),
            "message_id": done.get("message_id"),
            "trace_id": done.get("trace_id"),
            "events": events,
        }
        return await _reply(
            speech,
            result,
            {"stt_ms": stt_ms, "answer_ms": int((time.monotonic() - t1) * 1000)},
            t0,
        )


async def _save_spoken_lead(
    experience, tenant: str, conv: str, session: str | None, state: dict, lang: str
) -> tuple[str, str]:
    """The person said yes to the number read back: contact consent, then the lead, in
    Module C. In a thread, because Module C may call back into this service."""
    number = state["voice_lead"]["number"]
    state["voice_lead"] = None  # never kept after this turn, saved or not
    if not session:
        return spoken_lead.t("error", lang), "lead_error"
    try:
        await asyncio.to_thread(
            experience.consent,
            tenant,
            session,
            "contact",
            spoken_lead.CONSENT_TEXT_VERSION,
            "web_voice",
        )
        await asyncio.to_thread(
            experience.lead,
            tenant,
            session,
            {
                "phone": "+91" + number,
                "conversation_id": conv,
                "program_interest": state.get("programs", []),
            },
        )
    except ExperienceError:
        return spoken_lead.t("error", lang), "lead_error"
    return spoken_lead.t("saved", lang), "lead_saved"


async def _reply(speech: voice.Speech, result: dict, timings: dict, t0: float) -> dict:
    t2 = time.monotonic()
    try:
        if result.get("language", "en") not in voice.SPOKEN_LANGUAGES:
            raise LookupError("no voice for this language")  # shown as text instead
        wav = await asyncio.to_thread(speech.speak, result["answer"])
        result["audio_wav_base64"] = base64.b64encode(wav).decode()
    except Exception:  # noqa: BLE001 - PRD F2: on TTS failure show the text answer
        result["audio_wav_base64"] = None
    timings["tts_ms"] = int((time.monotonic() - t2) * 1000)
    timings["total_ms"] = int(
        (time.monotonic() - t0) * 1000
    )  # end of speech -> audio ready (F2.5)
    result["timings"] = timings
    return result


class SpeakIn(BaseModel):
    text: str = Field(min_length=1, max_length=1500)


@router.post("/voice/speak", summary="Read an answer aloud (local text-to-speech, WAV)")
async def speak(
    body: SpeakIn,
    who: Caller = Depends(caller),
    speech: voice.Speech = Depends(speech_dep),
):
    try:
        wav = await asyncio.to_thread(speech.speak, body.text)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(503, f"Voice is not available: {exc}") from None
    return Response(wav, media_type="audio/wav")
