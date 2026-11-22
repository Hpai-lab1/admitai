"""One spoken question, after speech-to-text: the part every voice channel shares (PRD F2.10).

Push-to-talk over HTTP (routes/voice.py) and the LiveKit worker (voice_worker.py) both call
`spoken_answer`, so misheard audio, the spoken rating, digit-by-digit number capture (F2.9) and the
engine itself behave the same whichever way the audio arrived.

`spoken_answer` yields, in order:
- {"sentence": text} for every sentence to say, as soon as the engine releases it (the LiveKit
  worker speaks each one at once: F2.4 / 9.3 "audio starts after the first sentence");
- one final {"result": {...}} with the transcript, outcome, sources and ids.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import AsyncIterator

from . import language, spoken_lead, store, voice
from .auth import Caller
from .engine import Deps, Forbidden, handle_turn
from .experience_client import ExperienceError


class NotYourConversation(Exception):
    """The conversation belongs to another visitor session."""


@dataclass
class VoiceVisitor:
    tenant: str
    conversation_id: str
    session_id: str | None


def reply_language(chosen: str | None, heard: voice.Transcript) -> str:
    """A first guess at the reply's language, for the fixed messages said before the engine runs
    (the engine decides for the answer itself: language.decide). Hindi or Kannada speech is
    answered in that language; English speech in the language the visitor chose."""
    said = language.detect(heard.text).language
    if said in ("hi", "kn"):
        return said
    if heard.provider != "local" and heard.language in ("hi", "kn"):
        return heard.language  # Hindi or Kannada heard, written in English letters
    if chosen in language.SUPPORTED:
        return chosen
    return heard.language if heard.language in ("hi", "kn") else "en"


async def spoken_answer(
    deps: Deps,
    experience,
    who: VoiceVisitor,
    heard: voice.Transcript,
    lang: str,
    expect: str | None = None,
    channel: str = "web_voice",
    chosen: str | None = None,
    english_reply: str | None = None,
) -> AsyncIterator[dict]:
    """`lang`: the language of the fixed messages said before the engine runs (reply_language).
    `chosen`: the visitor's pick (EN / हिं / ಕ), passed to the engine as the reply preference for
    English speech; the result's `language` is the one the engine answered in. `english_reply`:
    the reply language the visitor set for English speech (wins over the English itself)."""
    chosen = chosen if chosen is not None else lang
    db = deps.settings.database_url
    conv_row = await asyncio.to_thread(
        store.open_conversation,
        db,
        who.tenant,
        who.conversation_id,
        who.session_id,
        channel,
    )
    if conv_row is None:
        raise NotYourConversation()
    state = dict(conv_row["state"] or {})

    def fixed(answer: str, outcome: str, **extra) -> list[dict]:
        result = {
            "transcript": heard.text,
            "language": lang,
            "answer": answer,
            "outcome": outcome,
            "offer_handoff": False,
            "sources": [],
            **extra,
        }
        return [{"sentence": answer}, {"result": result}]

    if not heard.text or heard.confidence < voice.MIN_CONFIDENCE:
        misses = state.get("voice_misses", 0) + 1
        await asyncio.to_thread(
            store.update_state,
            db,
            who.tenant,
            who.conversation_id,
            {"voice_misses": misses},
        )
        key = "offer_chat" if misses >= 2 else "not_heard"
        for ev in fixed(voice.msg(key, lang), "not_heard"):
            if "result" in ev:
                ev["result"]["offer_handoff"] = misses >= 2
            yield ev
        return
    await asyncio.to_thread(
        store.update_state, db, who.tenant, who.conversation_id, {"voice_misses": 0}
    )
    # Spoken end-of-chat rating: read the number, answer nothing, never call the model.
    if expect == "rating":
        n = spoken_lead.spoken_rating(heard.text)
        words = spoken_lead.RATING_TEXT.get(lang, spoken_lead.RATING_TEXT["en"])
        text = words["thanks"].format(n=n) if n else words["unclear"]
        for ev in fixed(text, "rating" if n else "rating_unclear", rating=n):
            yield ev
        return
    # F2.9: a call-back request and the number, read back digit by digit, before the engine.
    # These turns are not written to the transcript: the number is personal data.
    reply, action = spoken_lead.step(state, heard.text, lang)
    outcome = "lead_capture"
    if action == "save":
        reply, outcome = await save_spoken_lead(experience, who, state, lang)
    if reply is not None:
        await asyncio.to_thread(
            store.update_state,
            db,
            who.tenant,
            who.conversation_id,
            {"voice_lead": state.get("voice_lead")},
        )
        for ev in fixed(reply, outcome):
            yield ev
        return
    caller = Caller(who.tenant, who.session_id, "visitor")
    answer, done, events = "", {}, {}
    try:
        async for ev in handle_turn(
            deps,
            caller,
            who.conversation_id,
            heard.text,
            chosen,
            "web_voice",
            english_reply=english_reply,
        ):
            if ev["event"] == "token":
                answer += ev["data"]["text"]
                yield {"sentence": ev["data"]["text"]}
            elif ev["event"] == "done":
                done = ev["data"]
            else:
                events[ev["event"]] = ev["data"]
    except Forbidden:
        raise NotYourConversation() from None
    yield {
        "result": {
            "transcript": heard.text,
            "language": done.get("language") or lang,
            "input_language": done.get("input_language"),
            "output_mode": done.get("output_mode"),
            "answer": answer.strip(),
            "outcome": done.get("outcome"),
            "offer_handoff": done.get("offer_handoff", False),
            "sources": done.get("sources", []),
            "message_id": done.get("message_id"),
            "trace_id": done.get("trace_id"),
            "events": events,
        }
    }


async def save_spoken_lead(
    experience, who: VoiceVisitor, state: dict, lang: str
) -> tuple[str, str]:
    """The person said yes to the number read back: contact consent, then the lead, in
    Module C. In a thread, because Module C may call back into this service."""
    number = state["voice_lead"]["number"]
    state["voice_lead"] = None  # never kept after this turn, saved or not
    if not who.session_id:
        return spoken_lead.t("error", lang), "lead_error"
    try:
        await asyncio.to_thread(
            experience.consent,
            who.tenant,
            who.session_id,
            "contact",
            spoken_lead.CONSENT_TEXT_VERSION,
            "web_voice",
        )
        await asyncio.to_thread(
            experience.lead,
            who.tenant,
            who.session_id,
            {
                "phone": "+91" + number,
                "conversation_id": who.conversation_id,
                "program_interest": state.get("programs", []),
            },
        )
    except ExperienceError:
        return spoken_lead.t("error", lang), "lead_error"
    return spoken_lead.t("saved", lang), "lead_saved"


# Above this, the programme list is left out of the speech-to-text hint. Measured on 13 Nov 2026
# (RUAS, 213 programmes, 18,000 characters, cut to 800 by the hint): with the list, 5 of 10
# English questions were heard right and Whisper wrote programme names it was never told
# ("LL M", "Oral Implantology"); with the base vocabulary only, 9 of 10. A short list (a small
# college) fits whole and still helps with its programme names.
MAX_VOCABULARY_CHARS = 400


async def vocabulary(deps: Deps, tenant: str) -> str:
    """Programme names and aliases, as a hint for speech-to-text, when the list is short."""
    from .clients import UpstreamError

    try:
        catalogue = await deps.knowledge.programs(tenant)
    except UpstreamError:
        return ""
    words = " ".join(
        f"{p['name']} {' '.join(p.get('aliases', []))}" for p in catalogue["items"]
    )
    return words if len(words) <= MAX_VOCABULARY_CHARS else ""
