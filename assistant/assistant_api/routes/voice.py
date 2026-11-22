"""Voice API (PRD F2, 12.1 POST /voice/token): push-to-talk turns and spoken answers."""

from __future__ import annotations

import asyncio
import base64
import re
import time
from datetime import datetime, timedelta, timezone
from typing import Literal

import jwt
from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import Response
from pydantic import BaseModel, Field

from .. import audio_store, livekit, policy, retell, store, voice, voice_turns
from .. import speech as speech_mod
from ..auth import Caller, caller, service_only, visitor_limits
from ..engine import Deps
from ..experience_client import ExperienceError
from .chat import deps_dep

router = APIRouter(tags=["voice"])
TOKEN_MINUTES = 10
MAX_AUDIO_BYTES = 5 * 1024 * 1024  # about a minute of compressed speech


def speech_dep(request: Request) -> voice.Speech:
    return request.app.state.speech


class TokenIn(BaseModel):
    conversation_id: str = Field(min_length=1, max_length=100)
    # PRD F2.6: the visitor ticked "keep a recording of my questions". Only honoured when the
    # college turned audio storage on; recorded as Module C consent before anything is stored.
    audio_consent: bool = False
    # The language the visitor chose (EN / हिं / ಕ): the LiveKit worker listens for it from the
    # first word, as push-to-talk does with its form field.
    language: Literal["en", "hi", "kn"] | None = None
    # True only when the visitor starts a hands-free call: a Retell web call is created for it.
    # Push-to-talk asks for a token without it (seen 18 Nov 2026: every push-to-talk turn created
    # a Retell call that nobody joined, "error_user_not_joined").
    call: bool = False


def _voice_external(database_url: str, tenant: str) -> set[str]:
    """Outside voice services this college opted in to (policy.voice_external)."""
    return policy.voice_external(
        store.tenant_config(database_url, tenant).get("model_policy")
    )


@router.post(
    "/voice/token", summary="Short-lived voice token for this visitor and conversation"
)
def voice_token(
    body: TokenIn,
    request: Request,
    who: Caller = Depends(caller),
    deps: Deps = Depends(deps_dep),
):
    s = deps.settings
    if who.kind == "visitor":  # PRD 12.3: per-IP and per-tenant limits, as for chat
        visitor_limits(request, who.tenant_id)
    # The room carries this conversation's answers and captions: only its own visitor may join.
    # (Push-to-talk turns check this too, but a room token is enough to listen in.)
    if store.belongs_to_another_session(
        s.database_url, who.tenant_id, body.conversation_id, who.session_id
    ):
        raise HTTPException(403, "This conversation belongs to another session")
    policy = audio_store.policy(s.database_url, who.tenant_id)
    record = False
    if policy["store_audio"] and body.audio_consent and who.session_id:
        try:
            request.app.state.experience.consent(
                who.tenant_id,
                who.session_id,
                audio_store.CONSENT_PURPOSE,
                audio_store.CONSENT_TEXT_VERSION,
                "web_voice",
            )
            audio_store.consented(s.database_url, who.tenant_id, who.session_id)
            record = True
        except ExperienceError:
            record = False  # no consent on record: nothing is stored
    now = datetime.now(timezone.utc)
    claims = {
        "typ": "voice",
        "tid": who.tenant_id,
        "sid": who.session_id,
        "cid": body.conversation_id,
        "iat": now,
        "exp": now + timedelta(minutes=TOKEN_MINUTES),
    }
    if record:
        claims["aud_rec"] = True
    token = jwt.encode(claims, s.voice_token_secret, algorithm="HS256")
    out = {
        "token": token,
        "expires_in": TOKEN_MINUTES * 60,
        # What the widget may offer (F2.6): "off" (the college keeps no audio), "offered" (ask
        # the visitor), "on" (this visitor consented; their questions are kept).
        "audio_recording": "on"
        if record
        else ("offered" if policy["store_audio"] else "off"),
        "audio_days": policy["audio_days"] if policy["store_audio"] else None,
    }
    if (
        body.call
        and s.retell_api_key
        and s.retell_agent_id
        and "retell" in _voice_external(s.database_url, who.tenant_id)
    ):
        # Retell AI does the listening and speaking; answers come from /voice/retell/llm.
        try:
            web = retell.create_web_call(
                s.retell_api_key,
                s.retell_agent_id,
                {"tid": who.tenant_id, "cid": body.conversation_id},
            )
            retell.remember(
                web["call_id"],
                retell.RetellCall(
                    who.tenant_id,
                    body.conversation_id,
                    who.session_id,
                    body.language,
                    time.time(),
                ),
                s.database_url,
            )
            out["retell"] = web
        except retell.RetellError as exc:
            out["retell_error"] = str(exc)  # push-to-talk still works
    if s.livekit_url and s.livekit_api_key and s.livekit_api_secret:
        # PRD F2.1 / 12.1: a short-lived LiveKit room token for this visitor and conversation.
        # The push-to-talk token above stays, so a widget without WebRTC keeps working.
        room = livekit.room_name(who.tenant_id, body.conversation_id)
        meta = {
            "tid": who.tenant_id,
            "cid": body.conversation_id,
            "sid": who.session_id,
        }
        if record:
            meta["aud_rec"] = True
        if body.language:
            meta["lang"] = body.language
        out["livekit"] = {
            "url": s.livekit_url,
            "room": room,
            "token": livekit.room_token(
                s.livekit_api_key,
                s.livekit_api_secret,
                room,
                identity=f"visitor-{who.session_id or 'service'}",
                metadata=meta,
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


def wrong_script(text: str, language: str) -> bool:
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
    response_language: Literal["en", "hi", "kn"] | None = Form(
        None,
        description="The language the visitor wants replies in when they speak English "
        "(Hindi and Kannada speech is answered in that language). Default: `language`.",
    ),
    output: Literal["text", "voice", "both"] | None = Form(
        None,
        description="text: no audio is made; voice or both: the answer comes with audio in its "
        "own language when a voice exists for it. Default: the visitor's choice in the "
        "conversation, else both.",
    ),
    deps: Deps = Depends(deps_dep),
    speech: voice.Speech = Depends(speech_dep),
):
    claims = _voice_claims(token, deps.settings.voice_token_secret)
    tenant, conv = claims["tid"], claims["cid"]
    # Sarvam hears and speaks only for a college that opted in; otherwise local Whisper and Piper.
    speech = speech_mod.for_college(
        speech,
        await asyncio.to_thread(_voice_external, deps.settings.database_url, tenant),
    )
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
        vocab = await voice_turns.vocabulary(deps, tenant)
        try:
            # The language the visitor chose (EN, हिं, ಕ) is what the speech model listens for,
            # English too: left to guess, it once heard an English-mode visitor as Kannada.
            hint = language if language in ("en", "kn", "hi") else None
            heard = await asyncio.to_thread(
                speech_mod.transcribe, speech, data, vocab, hint, audio.content_type
            )
            # Told "Kannada", a small speech model can still write the words in another script
            # (Urdu was seen). That is a mishearing, not a question: ask again, never answer it.
            # Sarvam detects the language itself, so its Kannada for an English pick is right.
            if hint and heard.provider == "local" and wrong_script(heard.text, hint):
                heard = voice.Transcript(heard.text, hint, -10.0)
        except RuntimeError as exc:
            raise HTTPException(503, f"Voice is not available: {exc}") from None
        stt_ms = int((time.monotonic() - t0) * 1000)
        chosen = response_language or (
            language if language in ("en", "hi", "kn") else None
        )
        lang = voice_turns.reply_language(chosen, heard)
        who = voice_turns.VoiceVisitor(tenant, conv, claims.get("sid"))
        t1 = time.monotonic()
        result: dict = {}
        try:
            async for ev in voice_turns.spoken_answer(
                deps,
                request.app.state.experience,
                who,
                heard,
                lang,
                expect,
                chosen=chosen,
                english_reply=response_language,
            ):
                result = ev.get("result", result)
        except voice_turns.NotYourConversation:
            raise HTTPException(
                403, "This conversation belongs to another session"
            ) from None
        if claims.get("aud_rec") and heard.text:
            # F2.6: the visitor consented when the token was issued (Module C has the record).
            await asyncio.to_thread(
                keep_audio,
                deps,
                tenant,
                conv,
                claims.get("sid"),
                "web_voice",
                data,
                heard.text,
            )
        timings = {"stt_ms": stt_ms}
        if "message_id" in result:
            timings["answer_ms"] = int((time.monotonic() - t1) * 1000)
        mode = output or result.get("output_mode") or "both"
        result["providers"] = {"stt": heard.provider, "tts": None}
        return await _reply(speech, result, timings, t0, spoken=mode != "text")


def keep_audio(
    deps: Deps,
    tenant: str,
    conv: str,
    session: str | None,
    channel: str,
    data: bytes,
    transcript: str,
) -> str | None:
    """F2.6: store one consented recording, if the college still has storage turned on."""
    policy = audio_store.policy(deps.settings.database_url, tenant)
    if not policy["store_audio"]:
        return None
    return audio_store.save(
        deps.settings.database_url,
        tenant,
        conv,
        session,
        channel,
        data,
        transcript,
        policy["audio_days"],
    )


async def _reply(
    speech: voice.Speech, result: dict, timings: dict, t0: float, spoken: bool = True
) -> dict:
    t2 = time.monotonic()
    lang = result.get("language", "en")
    result["audio_wav_base64"] = None
    if spoken:
        try:
            if not speech_mod.can_speak(speech, lang):
                raise LookupError("no voice for this language")  # shown as text instead
            wav = await asyncio.to_thread(
                speech_mod.speak_wav, speech, result["answer"], lang
            )
            result["audio_wav_base64"] = base64.b64encode(wav).decode()
            if "providers" in result:
                result["providers"]["tts"] = speech_mod.provider_names(speech, lang)[
                    "tts"
                ]
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
    # The answer's language (the chat's `done.language`): its own voice reads it (speech.py).
    language: Literal["en", "hi", "kn"] = "en"


@router.post(
    "/voice/speak",
    summary="Read an answer aloud in its language (WAV): Piper for English, Sarvam for Hindi "
    "and Kannada when configured",
)
async def speak(
    body: SpeakIn,
    who: Caller = Depends(caller),
    speech: voice.Speech = Depends(speech_dep),
    deps: Deps = Depends(deps_dep),
):
    speech = speech_mod.for_college(
        speech,
        await asyncio.to_thread(
            _voice_external, deps.settings.database_url, who.tenant_id
        ),
    )
    if not speech_mod.can_speak(speech, body.language):
        raise HTTPException(
            422, f"No voice is configured for {body.language}; show the text instead"
        )
    try:
        wav = await asyncio.to_thread(
            speech_mod.speak_wav, speech, body.text, body.language
        )
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(503, f"Voice is not available: {exc}") from None
    return Response(wav, media_type="audio/wav")
