"""One conversation turn (PRD 7.2): guardrail -> router -> retrieval -> one answer call ->
sentence-buffered, checked streaming -> lead rules -> trace. Yields SSE events:

  token              {"text"}  one checked sentence (the widget appends it)
  handoff_requested  {"queue", "department", "reason"}
  consent_required   {"given_by": "guardian", "reason": "minor"}
  lead_prompt        {"score", "band", "program_interest"}
  done               {"message_id", "conversation_id", "trace_id", "confidence", "offer_handoff",
                      "sources", "language", "outcome", "route"}

Hot-path model calls: at most two (optional router call when rules are inconclusive, and the
answer call); the fallback model is used only if the answer call fails before any sentence.
One exception, for a college that opts in (model_policy.general_answers): when its information
has nothing, one more short Gemini call writes a labelled general answer (_general_answer).

Languages (language.py): the reply's language is decided apart from the language the visitor
used. A Hindi or Kannada reply for a college that opted in to Gemini is the checked English
answer translated by Gemini in one call (_translated), kept only if every number survives; the
model router is skipped on such turns. A Hindi or Kannada *question* there is first put into
English (_english_question, TRANSLATE_QUESTIONS), so it is searched and checked as English: that
turn is three model calls (question, answer, translation), the one measured exception to the
two-call budget, and only for colleges that opted in. An English reply, a catalogue template or
a fixed message is never translated.

"Did you mean ...?" (suggest.py): a question naming no programme, with words close to one (a
misheard or misspelt name), gets the corrected question back instead of an answer, at no model
cost; "yes" asks it, "no" asks the visitor to say it again.
"""

from __future__ import annotations

import asyncio
import logging
import re
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from typing import AsyncIterator, Callable

from . import hours, policy, store, scope, suggest
from .auth import Caller
from .checks import (
    _PROMISE,
    ELIGIBILITY_VERDICT,
    Source,
    check_sentence,
    is_refusal,
    numbers_in,
    relevant,
    split_sentences,
)
from . import direct

from .clients import LLM, TOKEN_USAGE, Knowledge, UpstreamError
from .config import Settings
from .context import (
    RECENT_MESSAGES,
    build_sources,
    conversation_summary,
    is_follow_up,
    retrieval_query,
    sources_block,
)
from .guard import RateLimiter, check_input
from .knowledge import with_facts
from .language import Preferences
from .language import decide as decide_language
from .language import detect as detect_input
from .language import preferences as asked_preferences
from .leads import detect_minor, score_lead, scoring_rules, should_prompt
from .prompts import (
    AGENT_VERSION,
    ENGLISH_ONLY_NOTE,
    GENERAL_LANGUAGE,
    GENERAL_PROMPT,
    SPELLING_PROMPT,
    NO_ANSWER,
    PROMPT_VERSION,
    QUESTION_PROMPT,
    TRANSLATE_PROMPT,
    looks_like_no_answer,
    message,
    system_prompt,
    user_prompt,
)
from .router import ROUTER_PROMPT, Route, parse_model_route, route

log = logging.getLogger("admitai.engine")

_HEDGE = re.compile(
    r"\b(not sure|not certain|don't have (that|this|reliable) information)\b|पक्का पता नहीं",
    re.I,
)
_MARK = re.compile(r"\[(\d{1,2})\]")
# A marker at the start of a sentence belongs to the previous one (or to nothing); small models
# often open an answer with "[1]". It must not count as this sentence's citation.
_LEADING_MARKS = re.compile(r"^\s*(?:\[\d{1,2}\]\s*)+")
# Placeholder markers a small model copies from instructions ("[n]", "[source]") are noise.
_PLACEHOLDER_MARKS = re.compile(r"\s*\[(?:n|N|source|number|x)\]")
# A pleasantry with no facts in it ("Welcome to the admissions desk!", "Sure!", "Great question.")
# is dropped, not checked: seen live, Gemini opened with one, it had no citation, and the whole
# (correct, cited) answer was blocked. Only short sentences without digits that start like a
# greeting qualify, so a factual sentence is never released unchecked; at worst one is dropped.
_PLEASANTRY = re.compile(
    r"^(?:hi|hello|hey|welcome|greetings|namaste|thanks?|thank you|sure|certainly|of course|"
    r"absolutely|great question|good question|happy to help|glad you asked)\b[^0-9\[\]]{0,100}$",
    re.IGNORECASE,
)


# Calls from Module A (evaluation runs) and operator tooling are not visitors.
ORIGIN = {"knowledge": "evaluation", "ops": "operator"}


class Forbidden(Exception):
    pass


@dataclass
class Deps:
    settings: Settings
    knowledge: Knowledge
    llm: LLM
    limiter: RateLimiter
    clock: Callable[[], datetime] | None = (
        None  # the time, for office hours (tests set it)
    )


@dataclass
class Turn:
    """Everything recorded about one turn (the trace)."""

    trace_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    message_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    started: float = field(default_factory=time.monotonic)
    spans: list[dict] = field(default_factory=list)
    released: list[str] = field(default_factory=list)
    numbering: dict[int, int] = field(default_factory=dict)
    checks: list[dict] = field(default_factory=list)
    first_sentence_ms: int | None = None
    relevance_checked: bool = False
    model: str | None = None
    outcome: str = "answered"
    offer_handoff: bool = False
    handoff_reason: str | None = None

    def span(self, name: str, start: float, **attrs) -> None:
        self.spans.append(
            {"name": name, "ms": round((time.monotonic() - start) * 1000, 1), **attrs}
        )

    def ms(self) -> int:
        return int((time.monotonic() - self.started) * 1000)


def _ev(event: str, data: dict) -> dict:
    return {"event": event, "data": data}


def _renumber(sentence: str, turn: Turn) -> str:
    """Display numbers follow the order sources are first cited in the released text."""

    def sub(m: re.Match) -> str:
        n = int(m.group(1))
        if n not in turn.numbering:
            turn.numbering[n] = len(turn.numbering) + 1
        return f"[{turn.numbering[n]}]"

    return _MARK.sub(sub, sentence)


async def _model_route(deps: Deps, text: str, turn: Turn) -> str | None:
    start = time.monotonic()
    try:
        reply = await deps.llm.complete(
            deps.settings.router_model,
            [{"role": "user", "content": ROUTER_PROMPT.format(q=text)}],
            max_tokens=4,
            timeout=5,
        )
    except (UpstreamError, asyncio.TimeoutError):
        reply = ""
    result = parse_model_route(reply)
    turn.span("router_model", start, reply=reply[:20], result=result)
    return result


# Answers being written for real people right now, on this process (GET /load). Module A's
# evaluation gate shares the model service with them, so it waits between test questions while
# this is above zero: visitors come first. The gate's own turns use "eval-" conversations.
EVAL_PREFIX = "eval-"
_live_turns = 0
# Channels whose answers are spoken (voice style, F2.3): the widget's voice (push-to-talk,
# Retell, LiveKit) and phone calls (Vapi).
SPOKEN_CHANNELS = ("web_voice", "phone")


def live_turns() -> int:
    return _live_turns


def _tokens() -> dict:
    """This turn's tokens, or None for each when the gateway reported none."""
    tally = TOKEN_USAGE.get() or {}
    return {
        "prompt_tokens": tally.get("prompt") or None,
        "completion_tokens": tally.get("completion") or None,
    }


async def handle_turn(
    deps: Deps,
    caller: Caller,
    conversation_id: str,
    text: str,
    requested_language: str | None = None,
    channel: str = "web_chat",
    only_language: str | None = None,
    english_reply: str | None = None,
) -> AsyncIterator[dict]:
    """`only_language`: a channel that speaks one language only (Vapi phone calls: English)
    gets every reply in it, whatever the caller speaks or asks for. `english_reply`: the language
    English questions are answered in when the visitor set one (voice "reply language")."""
    global _live_turns
    TOKEN_USAGE.set({"prompt": 0, "completion": 0})  # this turn's model usage (F9.4)
    live = not conversation_id.startswith(EVAL_PREFIX)
    if live:
        _live_turns += 1
    try:
        async for ev in _turn(
            deps,
            caller,
            conversation_id,
            text,
            requested_language,
            channel,
            only_language,
            english_reply,
        ):
            yield ev
    finally:
        if live:
            _live_turns -= 1


async def _turn(
    deps: Deps,
    caller: Caller,
    conversation_id: str,
    text: str,
    requested_language: str | None = None,
    channel: str = "web_chat",
    only_language: str | None = None,
    english_reply: str | None = None,
) -> AsyncIterator[dict]:
    s = deps.settings
    turn = Turn()
    spoken = channel in SPOKEN_CHANNELS  # short answers, said aloud (F2.3)
    tenant = caller.tenant_id
    # The database layer is synchronous (psycopg): every call runs in a worker thread so that a
    # slow query never stalls the other conversations on this process (PRD 10.2 concurrency).
    cfg = await asyncio.to_thread(store.tenant_config, s.database_url, tenant)
    conv = await asyncio.to_thread(
        store.open_conversation,
        s.database_url,
        tenant,
        conversation_id,
        caller.session_id,
        channel,
        ORIGIN.get(caller.service, "visitor"),
    )
    if conv is None:
        raise Forbidden("This conversation belongs to another session")
    state = dict(conv["state"] or {})
    history = await asyncio.to_thread(
        store.history, s.database_url, tenant, conversation_id, s.history_turns
    )

    # "Did you mean ...?" was asked last turn: "yes" asks the (first) suggested question, "2" or
    # "the second one" that one, "no" asks the visitor to say it again; anything else is a new
    # question. The visitor's own words are what the transcript keeps.
    asked = text
    pending = state.pop("did_you_mean", None)
    pending_codes = state.pop("did_you_mean_codes", None) or []
    picked = suggest.choice(text, len(pending)) if pending else None
    confirmed = picked is not None
    declined = bool(pending) and not confirmed and bool(suggest.NEGATIVE.match(text))
    # The programme the confirmed suggestion was about: used as it is, not found again in the
    # suggestion's wording (seen live with voice: "BBA owners" -> "Did you mean ... BBA honors?"
    # -> "Yes" -> "BBA honors" matched no programme and the answer failed its checks).
    confirmed_code = (
        pending_codes[picked] if confirmed and picked < len(pending_codes) else None
    )
    if confirmed:
        text = pending[picked]
    # The last answer offered a person ("Would you like someone from the admissions team to ...?"):
    # "yes" accepts it and "no" declines it. Seen live: "yes" was searched together with the
    # previous question and the same answer came again.
    offered = state.pop("offered_person", False) and not pending
    short = len(text.split()) <= 4
    accepted = offered and short and bool(suggest.AFFIRMATIVE.match(text))
    turned_down = offered and short and bool(suggest.NEGATIVE.match(text))
    if accepted:
        text = "I want to talk to a person"

    start = time.monotonic()
    # The reply's language (language.py), kept apart from the language the visitor used: asked
    # for in the conversation ("reply in Kannada"), else the question's own Hindi or Kannada,
    # else the widget's pick for English questions. "Text only" / "reply with voice" likewise.
    prefs = (
        Preferences(None, None, text, False)
        if confirmed or accepted
        else asked_preferences(text)
    )
    if prefs.output:
        state["output_mode"] = prefs.output
    repeat = False
    if (
        prefs.only
        and prefs.language
        and state.get("last_question")
        and not only_language
    ):
        # "Kannada please" right after an answer: the same question again, in that language.
        text, repeat = state["last_question"], True
    elif prefs.question:
        text = prefs.question
    detected = detect_input(prefs.question or asked)
    if only_language:
        language, language_reason = only_language, "channel"
    else:
        language, language_reason = decide_language(
            detected,
            requested_language,
            state,
            prefs.language,
            english_reply,
            spoken=spoken,
        )
    translated = policy.translated_languages(cfg.get("model_policy"), s)
    g = check_input(text, language)
    turn.span(
        "input_guardrail",
        start,
        ok=g.ok,
        reason=g.reason,
        language=language,
        input_language=detected.language,
        code_mixed=detected.code_mixed,
        language_reason=language_reason,
        repeated=repeat or None,
    )
    sources: list[Source] = []
    decision = None
    catalogue: dict = {"items": [], "general_dates": []}
    reply_parts: list[str] = []

    async def say(sentence: str) -> dict:
        reply_parts.append(sentence)
        if turn.first_sentence_ms is None:
            turn.first_sentence_ms = turn.ms()
        return _ev("token", {"text": sentence + " "})

    key = caller.session_id or f"{tenant}:{conversation_id}"
    if not deps.limiter.allow(key):
        turn.outcome = "refused"
        yield await say(message("rate_limited", language))
    elif not g.ok:
        turn.outcome = "refused"
        yield await say(message(g.reason, language))
    elif declined:
        turn.outcome = "clarify"
        turn.span("did_you_mean", start, declined=True)
        yield await say(message("did_you_mean_no", language))
    elif turned_down:
        turn.outcome = "clarify"
        turn.span("offer_person", start, declined=True)
        yield await say(message("offer_declined", language))
    elif prefs.only and not repeat:
        # Only a preference ("Kannada please", "text only"), nothing to answer: confirm it in
        # the reply's language. Not a knowledge gap, so not no_answer.
        turn.outcome = "clarify"
        turn.span("preferences", start, language=prefs.language, output=prefs.output)
        if prefs.language:
            other = only_language and prefs.language != only_language
            yield await say(
                message("language_only" if other else "language_set", language)
            )
        if prefs.output:
            yield await say(message(f"output_{prefs.output}", language))
    else:
        # ---------------------------------------------------------- route
        start = time.monotonic()
        # A Hindi or Kannada question, at a college that opted in to Gemini: put into English
        # by Gemini (one short call, alongside the catalogue request), so routing, the search of
        # the college's English sources, the templates and the checks read it as English. The
        # visitor's own words are what the transcript keeps. Level 1 (local models only) keeps
        # its two calls: the question is searched as written there. It goes by the question's
        # language, not the reply's: a Kannada question on the English-only phone line (Vapi)
        # missed the hostel fee when searched as written (live, 17 Nov 2026).
        asked_in = detect_input(text).language
        understand = (
            asyncio.create_task(_english_question(deps, turn, cfg, text, asked_in))
            if s.translate_questions
            and asked_in in s.gemini_translated_languages
            and policy.uses_gemini(cfg.get("model_policy"), s)
            else None
        )
        try:
            catalogue = await deps.knowledge.programs(tenant)
        except UpstreamError as exc:
            turn.span("catalogue_error", start, error=str(exc))
            catalogue = {"items": [], "general_dates": []}
        if catalogue.get("unavailable"):
            turn.span("catalogue_error", start, error=f"no {catalogue['unavailable']}")
        if understand is not None:
            text = await understand or text
        # The previous program carries over only into a genuine follow-up ("what about its fees?").
        previous = (
            [p for p in catalogue["items"] if p["code"] in state.get("programs", [])]
            if is_follow_up(text, history)
            else []
        )
        if confirmed_code:
            previous = [p for p in catalogue["items"] if p["code"] == confirmed_code]
        decision = route(
            text, language, cfg["sensitive_topics"], catalogue["items"], previous
        )
        if confirmed_code and not decision.programs and previous:
            decision.programs = previous
        turn.span("router_rules", start, **decision.as_dict())
        if decision.programs:
            state["programs"] = [p["code"] for p in decision.programs]
        minor = detect_minor(text)
        if minor is not None and state.get("is_minor") != minor:
            state["is_minor"] = minor
            if minor:
                yield _ev(
                    "consent_required", {"given_by": "guardian", "reason": "minor"}
                )

        if confirmed:
            turn.span("did_you_mean", start, confirmed=True)
        # A misheard or misspelt programme name: suggest the corrected question, no model call.
        offers = (
            []
            if decision.programs or decision.handoff or confirmed
            else await asyncio.to_thread(suggest.did_you_mean, text, catalogue["items"])
        )
        if len(offers) == 1 and offers[0].exact:
            # Named right, only written differently ("B.Des" for "B.Des."): no need to ask.
            known = offers[0].program
            decision.programs = [known]
            state["programs"] = [known["code"]]
            for r in decision.routes:
                if r.role == "faculty" and r.department is None:
                    r.department = known.get("department")
            turn.span("did_you_mean", start, recognised=known["code"])
            offers = []
        if offers:
            state["did_you_mean"] = [o.question for o in offers]
            state["did_you_mean_codes"] = [o.program["code"] for o in offers]
            turn.outcome = "clarify"
            turn.span(
                "did_you_mean", start, options=[o.program["code"] for o in offers]
            )
            yield await say(_did_you_mean([o.question for o in offers], language))
        elif decision.handoff:
            turn.outcome = "handoff"
            turn.offer_handoff = True
            turn.handoff_reason = (
                "Asked to talk to a person"
                if decision.explicit_human
                else f"Sensitive topic: {decision.sensitive}"
            )
            yield await say(
                message("human" if decision.explicit_human else "sensitive", language)
            )
        else:
            # A Kannada-only visitor can get only a catalogue answer or the hand-off message
            # (same_language_only): neither needs the model router or the document search, so
            # both are skipped and the reply comes at once (it took 7.9 s for a misheard question).
            catalogue_only = (
                language in s.same_language_only
                and language not in s.native_answer_languages
                and language not in translated
            )
            # A reply to be translated already costs a second model call (the translation), so
            # the model router is skipped to stay within two calls per turn (PRD 7.5); the
            # rules still route, and the default route searches every source.
            if (
                decision.decided_by == "default"
                and s.use_model_router
                and len(text.split()) >= 3
                and not catalogue_only
                and language not in translated
                and understand
                is None  # the question's translation was this turn's extra call
            ):
                role = await _model_route(deps, text, turn)
                if role in ("admission", "faculty"):
                    decision.routes = [Route("general", role, 0.6, text)]
                    decision.decided_by = "model"
                elif role == "other":
                    decision.decided_by = "model"
            # ------------------------------------------------------ retrieve
            start = time.monotonic()
            query = retrieval_query(text, history)
            year = (
                _current_year(catalogue)
                if cfg["retrieval"].get("academic_year") == "current"
                else None
            )
            # Exact facts for the programmes this answer is about, from the agreed contract
            # (GET /catalogue/programs/{id}/facts) when the programme list did not carry them.
            if decision.programs:
                decision.programs = [
                    *await with_facts(deps.knowledge, tenant, decision.programs[:2]),
                    *decision.programs[2:],
                ]
            elif {r.intent for r in decision.routes} & {"fees", "programs"}:
                catalogue = {
                    **catalogue,
                    "items": await with_facts(
                        deps.knowledge, tenant, catalogue["items"]
                    ),
                }
            # F2.10: voice moved to the answer model gets a shorter context to stay fast.
            limit = (
                VOICE_SWITCHED_SOURCES
                if spoken and _voice_on_answer_model(cfg)
                else s.max_sources
            )
            # A plain catalogue question (a fee, the duration, an admission date) is answered from
            # the catalogue alone (direct.py), so the document search, 1-3 s, is skipped.
            catalogue_first = s.direct_catalogue_answers and (
                direct.answer(
                    text,
                    language,
                    decision.routes,
                    decision.programs,
                    build_sources(decision, catalogue, [], limit),
                    catalogue,
                )
                is not None
            )
            # One programme in focus: its own sources and the college-wide ones (scope.py).
            focused = (
                decision.programs[0]["code"] if len(decision.programs) == 1 else None
            )
            searches = (
                []
                if catalogue_only or catalogue_first
                else [
                    deps.knowledge.search(
                        tenant,
                        retrieval_query(r.text, history)
                        if len(decision.routes) > 1
                        else query,
                        top_k=scope.FOCUSED_TOP_K if focused else 6,
                        role=r.role,
                        department=r.department if r.role == "faculty" else None,
                        academic_year=year,
                    )
                    for r in decision.routes
                ]
            )
            results: list[dict] = []
            floors = cfg["retrieval"]
            dropped = 0
            searched: list[dict] = []  # every hit, for "did you mean" on a no-answer
            try:
                for res in await asyncio.gather(*searches):
                    searched.extend(res["items"])
                    floor = floors[
                        "min_score_multilingual"
                        if res.get("embedder") == "multilingual"
                        else "min_score"
                    ]
                    items = scope.focus(res["items"], focused, catalogue["items"])[:4]
                    kept = [i for i in items if i["vector_score"] >= floor]
                    dropped += len(items) - len(kept)
                    results.extend(kept)
            except UpstreamError as exc:
                turn.span("retrieval_error", start, error=str(exc))
            results.sort(key=lambda i: -i["score"])
            sources = build_sources(decision, catalogue, results, limit)
            turn.span(
                "retrieval",
                start,
                query=query,
                below_floor=dropped,
                sources=[
                    {
                        "n": x.n,
                        "title": x.citation["title"],
                        "score": x.citation.get("vector_score"),
                    }
                    for x in sources
                ],
            )
            summary = conversation_summary(
                history[:-RECENT_MESSAGES], _programme_names(catalogue, state)
            )
            if not sources and not catalogue_only:
                turn.outcome = "no_answer"
            else:
                answer_language = (
                    language if language in s.native_answer_languages else "en"
                )
                # A catalogue fact asked plainly is answered from the catalogue, no model
                # (direct.py); it still passes the same sentence check, or the model answers.
                plain = (
                    direct.answer(
                        text,
                        language,
                        decision.routes,
                        decision.programs,
                        sources,
                        catalogue,
                    )
                    if s.direct_catalogue_answers
                    else None
                )
                released = None
                if plain:
                    before = (
                        len(turn.checks),
                        dict(turn.numbering),
                        turn.relevance_checked,
                    )
                    generic = {w.lower() for w in cfg["college"].split()} | {
                        w.lower() for w in cfg.get("college_aliases", [])
                    }
                    # Relevance is how the template was chosen (the question asked exactly this),
                    # so the word-overlap test, which cannot read Kannada in English letters
                    # ("Arji Salislu Kone Yadinaam"), is not repeated; every other check runs.
                    turn.relevance_checked = True
                    verdict, released = _judge(turn, plain, sources, query, generic)
                    if verdict != "ok":
                        del turn.checks[before[0] :]
                        turn.numbering, turn.relevance_checked = before[1], before[2]
                        released = None
                # An approved curated answer that is clearly what was asked is given word for
                # word (direct.curated_answer): the model once reversed a "No" in rewriting one.
                # Every sentence still passes the same check, or the model answers as before.
                curated_released: list[str] = []
                if (
                    not released
                    and s.direct_curated_answers
                    and language == "en"
                    and answer_language == "en"
                ):
                    found = direct.curated_answer(sources, s.direct_curated_min_rerank)
                    if found:
                        before = (
                            len(turn.checks),
                            dict(turn.numbering),
                            turn.relevance_checked,
                        )
                        generic = {w.lower() for w in cfg["college"].split()} | {
                            w.lower() for w in cfg.get("college_aliases", [])
                        }
                        turn.relevance_checked = True  # the cross-encoder chose it
                        for part in found[0]:
                            verdict, ok_part = _judge(
                                turn, part, sources, query, generic
                            )
                            if verdict != "ok":
                                curated_released = []
                                break
                            curated_released.append(ok_part)
                        if not curated_released:
                            del turn.checks[before[0] :]
                            turn.numbering, turn.relevance_checked = (
                                before[1],
                                before[2],
                            )
                if released:
                    turn.model = None
                    turn.released.append(released)
                    yield await say(released)
                    turn.span("answer", start, model=None, direct="catalogue")
                elif curated_released:
                    turn.model = None
                    for part in curated_released:
                        turn.released.append(part)
                        yield await say(part)
                    turn.span("answer", start, model=None, direct="curated")
                else:
                    # A Kannada visitor gets Kannada only: the English answer is checked as
                    # usual, held back, then translated; a translation that changes any number or
                    # date is never shown (roadmap 22: the model cannot yet write Kannada safely).
                    translate = language in translated and answer_language != language
                    held: list[str] = []
                    # Kannada only: no translation to trust, so the question goes to a person,
                    # with the reason in Kannada, rather than an answer in English.
                    limited = (
                        not translate
                        and answer_language != language
                        and language in s.same_language_only
                    )

                    async def hold(sentence: str) -> None:
                        held.append(sentence)

                    if limited:
                        turn.outcome = "handoff"
                        turn.offer_handoff = True
                        turn.handoff_reason = (
                            f"Asked in {language}: only fees, course length and dates are "
                            "answered in that language for now"
                        )
                        released = True  # no "answer in English" note either
                        yield await say(message("language_limited", language))
                    if not limited:
                        async for ev in _answer(
                            deps,
                            turn,
                            cfg,
                            decision,
                            text,
                            answer_language,
                            history,
                            sources,
                            hold if translate else say,
                            relevance_text=query,
                            reply_language=language,
                            voice=spoken,
                            summary=summary,
                        ):
                            if ev is not None:
                                yield ev
                    if translate:
                        model = policy.translation_model(
                            cfg.get("model_policy"), s, language, turn.model
                        )
                        for part in await _translated(
                            deps,
                            turn,
                            held,
                            language,
                            model,
                            sarvam_fallback="sarvam"
                            in policy.voice_external(cfg.get("model_policy")),
                        ):
                            yield await say(part)
                        released = released or bool(held)
                if (
                    answer_language != language
                    and not released  # a catalogue answer is already in the visitor's language
                    and turn.outcome in ("answered", "partial")
                    and language in ENGLISH_ONLY_NOTE
                ):
                    yield await say(ENGLISH_ONLY_NOTE[language])
            # No answer: first the college's own questions closest to it (a typo such as "comdk",
            # a misheard word), then a labelled general answer if the college opted in, else the
            # usual no-answer with a person offered.
            related = (
                await _related(
                    deps,
                    turn,
                    cfg,
                    tenant,
                    text,
                    catalogue,
                    language,
                    # The turn's own search, when it was one search for this very question.
                    (query, searched) if len(searches) == 1 else None,
                )
                if turn.outcome == "no_answer" and not confirmed
                else []
            )
            if related:
                state["did_you_mean"] = related
                turn.outcome = "clarify"
                yield await say(_did_you_mean(related, language))
            elif turn.outcome == "no_answer":
                turn.offer_handoff = True
                turn.handoff_reason = "No reliable source for the question"
                general = await _general_answer(deps, turn, cfg, text, language, spoken)
                for part in general or [message("no_answer", language)]:
                    yield await say(part)

    # -------------------------------------------------------------- after the answer
    confidence = _confidence(turn, sources)
    if (
        turn.outcome == "answered"
        and confidence < cfg["escalation"]["confidence_below"]
    ):
        turn.offer_handoff = True
        turn.handoff_reason = turn.handoff_reason or "Low confidence"
    if turn.offer_handoff and decision is not None:
        # Faculty queue only with a known department; otherwise the Admission queue is the
        # default owner (PRD F3 error handling: "router uncertain").
        r0 = next(
            (r for r in decision.routes if r.role == "faculty" and r.department),
            None,
        )
        # F3.1 office-hours rule: a handoff is a callback at any hour (F3.4); outside office
        # hours the visitor is told when the team is back, and Module C is told too.
        office = hours.status(
            cfg.get("office_hours"),
            cfg.get("timezone"),
            deps.clock() if deps.clock else None,
        )
        if (
            office
            and not office["open"]
            and office["next_open"]
            and turn.outcome in ("handoff", "no_answer", "blocked", "error")
        ):
            when = hours.spoken_time(
                office["next_open"], language, deps.clock() if deps.clock else None
            )
            yield await say(
                message("closed", language).format(hours=office["hours"], when=when)
            )
        yield _ev(
            "handoff_requested",
            {
                "queue": "faculty" if r0 else "admission",
                "department": r0.department if r0 else None,
                "reason": turn.handoff_reason,
                "language": language,
                "office_hours": office,
            },
        )
    answered = turn.outcome in ("answered", "partial")
    if answered:
        state["answered_turns"] = state.get("answered_turns", 0) + 1
        state["last_question"] = text  # "say that in Hindi" asks it again (language.py)
    state["last_language"] = language
    if turn.offer_handoff and turn.outcome != "handoff":
        state["offered_person"] = (
            True  # the reply ended by offering a person: see "yes" above
        )
    if decision is not None:
        state["intents"] = sorted(
            set(state.get("intents", [])) | {r.intent for r in decision.routes}
        )
        state["asked_human"] = state.get("asked_human") or decision.explicit_human
    rules = scoring_rules(cfg.get("lead_scoring"))
    if should_prompt(
        state,
        answered,
        bool(decision and decision.sensitive),
        rules["prompt_after_answers"],
    ):
        state["lead_prompted"] = True
        lead = score_lead(
            len([h for h in history if h["sender"] == "user"]) + 1,
            state.get("intents", []),
            state.get("programs", []),
            bool(state.get("asked_human")),
            rules,
        )
        yield _ev(
            "lead_prompt",
            {
                **lead,
                "program_interest": state.get("programs", []),
                "is_minor": bool(state.get("is_minor")),
            },
        )

    cited = sorted(turn.numbering.items(), key=lambda kv: kv[1])
    by_n = {x.n: x for x in sources}
    shown = [
        {
            "n": display,
            "title": by_n[orig].citation["title"],
            "academic_year": by_n[orig].citation.get("academic_year"),
            "url": by_n[orig].citation.get("url"),
            "heading_path": by_n[orig].citation.get("heading_path"),
            "source_type": by_n[orig].citation["source_type"],
            "ref_id": by_n[orig].citation.get("ref_id"),
        }
        for orig, display in cited
        if orig in by_n
    ]
    reply = " ".join(reply_parts).strip()
    await asyncio.to_thread(
        store.save_turn,
        s.database_url,
        tenant,
        conversation_id,
        state,
        language,
        user_text=asked,
        user_language=detected.language,
        assistant={
            "id": turn.message_id,
            "content": reply,
            "language": language,
            "agent": "answer:" + "+".join(decision.roles) if decision else "guardrail",
            "agent_version": AGENT_VERSION,
            "prompt_version": PROMPT_VERSION,
            "model": turn.model,
            **_tokens(),
            "confidence": confidence,
            "trace_id": turn.trace_id,
            "latency_ms": turn.first_sentence_ms,
            "total_ms": turn.ms(),
            "route": decision.as_dict() if decision else None,
            "checks": turn.checks,
            "outcome": turn.outcome,
            "status": "handed_off" if turn.outcome == "handoff" else None,
        },
        # The exact source text the model saw, for the asynchronous grounding review (F1.4b).
        citations=[
            {**c, "content": by_n[orig].text}
            for c, (orig, _) in zip(shown, [x for x in cited if x[0] in by_n])
        ],
        spans=[{"name": "input", "masked": g.masked}, *turn.spans],
        summary=conversation_summary(
            [*history, {"sender": "user", "content": text}],
            _programme_names(catalogue, state),
        ),
    )
    yield _ev(
        "done",
        {
            "message_id": turn.message_id,
            "conversation_id": conversation_id,
            "trace_id": turn.trace_id,
            "confidence": confidence,
            "offer_handoff": turn.offer_handoff,
            "sources": shown,
            "language": language,
            # Input and response language apart (language.py), and the visitor's output choice
            # (text | voice | both, or None when never asked): the widget plays audio by it.
            "input_language": detected.language,
            "language_reason": language_reason,
            "code_mixed": detected.code_mixed,
            "output_mode": state.get("output_mode"),
            "outcome": turn.outcome,
            "route": decision.as_dict() if decision else None,
            "versions": {
                "agent": AGENT_VERSION,
                "prompt": PROMPT_VERSION,
                "model": turn.model,
            },
        },
    )


async def _answer(
    deps,
    turn,
    cfg,
    decision,
    text,
    language,
    history,
    sources,
    say,
    relevance_text,
    reply_language=None,
    voice=False,
    summary=None,
):
    """Stream one answer call, releasing only sentences that pass the checks. `language` is the
    language the model writes in; fixed messages use the visitor's `reply_language`."""
    s = deps.settings
    reply_language = reply_language or language
    msgs = [
        {
            "role": "system",
            "content": system_prompt(
                cfg["college"],
                cfg["assistant_name"],
                cfg["tone"],
                decision.roles,
                language,
                voice,
            ),
        },
        {
            "role": "user",
            "content": user_prompt(
                text,
                sources_block(sources),
                [
                    {"sender": h["sender"], "content": h["content"]}
                    for h in history[-RECENT_MESSAGES:]
                ],
                language,
                summary,
            ),
        },
    ]
    generic = {w.lower() for w in cfg["college"].split()} | {
        w.lower() for w in cfg.get("college_aliases", [])
    }
    chosen = policy.models(  # PRD 9.5, per college; F2.10 voice switch
        cfg.get("model_policy"), s, voice, _voice_on_answer_model(cfg)
    )
    # One extra try when the first sentence only lacked a source number: everything is still
    # checked, so this cannot release anything unverified. Not for voice, where the wait is felt,
    # and not after a model routing call (budget below).
    plan = [chosen.primary, chosen.fallback]
    # PRD 7.5: at most two model calls per turn. When the router already asked the model, the
    # answer gets no extra try, so the budget holds.
    asked_for_citation = voice or decision.decided_by == "model"
    for attempt, model in enumerate(plan):
        turn.model = model
        start = time.monotonic()
        buffer, full = "", ""
        first_chunk_ms: int | None = (
            None  # time to first token from the gateway (PRD 7.5)
        )
        held: list[str] = []  # uncited sentences waiting for the first cited one
        noted = False  # multi-part: the "not certain" note is said once
        retry = False
        chunks = None
        try:
            chunks = _with_timeout(
                deps.llm.stream(model, msgs, chosen.max_tokens, s.llm_timeout_s),
                s.llm_timeout_s,
            )
            finished = False
            while not finished:
                try:
                    chunk = await chunks.__anext__()
                    if first_chunk_ms is None:
                        first_chunk_ms = int((time.monotonic() - start) * 1000)
                    buffer += chunk
                    full += chunk
                    if _is_no_answer(full):
                        break
                    sentences, buffer = split_sentences(buffer)
                except StopAsyncIteration:
                    finished = True
                    sentences = split_sentences(buffer, final=True)[0]
                for sentence in sentences:
                    verdict, ok = _judge(
                        turn, sentence, sources, relevance_text, generic
                    )
                    if verdict == "skip":
                        continue
                    if verdict == "hold":
                        held.append(ok)
                        continue
                    if verdict == "irrelevant":
                        turn.outcome = "no_answer"
                        turn.span(
                            "answer",
                            start,
                            model=model,
                            attempt=attempt,
                            ttft_ms=first_chunk_ms,
                            stopped="not_relevant",
                        )
                        return
                    if (
                        verdict == "fail"
                        and not asked_for_citation
                        and not turn.released
                        and turn.checks[-1].get("reason") == "no citation"
                    ):
                        asked_for_citation = True
                        plan.insert(attempt + 1, model)
                        msgs = [
                            *msgs,
                            {"role": "assistant", "content": full},
                            {"role": "user", "content": CITE_AGAIN},
                        ]
                        turn.checks[-1]["retried"] = True
                        turn.span("answer", start, model=model, stopped="no_citation")
                        finished = True
                        retry = True
                        break
                    if verdict == "fail":
                        turn.offer_handoff = True
                        turn.handoff_reason = "An answer detail could not be verified"
                        if len(decision.routes) > 1:
                            # F3.1a: in a multi-part question only the failing part is handed
                            # off; the other parts are still answered, every sentence checked.
                            held.clear()
                            # The "not certain" note only when nothing verified was given yet:
                            # after verified sentences the unverified one is simply left out
                            # (19 Nov 2026: the note after good answers read as if every answer
                            # was unreliable). The "ask someone" offer stays with the answer.
                            if not noted and not turn.released:
                                noted = True
                                yield await say(message("not_certain", reply_language))
                            continue
                        # F1.4a: a single question stops at the first unverifiable sentence.
                        turn.outcome = "partial" if turn.released else "blocked"
                        if not turn.released:
                            yield await say(message("not_certain", reply_language))
                        turn.span(
                            "answer",
                            start,
                            model=model,
                            attempt=attempt,
                            ttft_ms=first_chunk_ms,
                            stopped="sentence_check",
                        )
                        return
                    for sentence_ok in [*held, ok]:
                        turn.released.append(sentence_ok)
                        yield await say(sentence_ok)
                    held.clear()
            if retry:
                continue
            turn.span(
                "answer",
                start,
                model=model,
                attempt=attempt,
                ttft_ms=first_chunk_ms,
                chars=len(full),
            )
            if not turn.released:
                turn.outcome = "blocked" if noted else "no_answer"
            elif (
                noted
                or any(_HEDGE.search(x) or is_refusal(x) for x in turn.released)
                or looks_like_no_answer(full)
            ):
                turn.outcome = "partial"
                turn.offer_handoff = True
                turn.handoff_reason = (
                    turn.handoff_reason or "Part of the question could not be answered"
                )
            return
        except (UpstreamError, TimeoutError) as exc:
            turn.span(
                "answer_error",
                start,
                model=model,
                attempt=attempt,
                ttft_ms=first_chunk_ms,
                error=str(exc) or "timeout",
            )
            # Give up only when no model is left: after a citation retry the plan is three long
            # (seen live: Gemini, Gemini again rate-limited, and the local fallback never tried).
            if turn.released or attempt >= len(plan) - 1:
                turn.outcome = "error" if not turn.released else "partial"
                turn.offer_handoff = True
                turn.handoff_reason = "The assistant was unavailable"
                yield await say(message("unavailable", reply_language))
                return
        finally:
            if chunks is not None:
                await chunks.aclose()  # stop the model generating once we stop reading


def _did_you_mean(questions: list[str], language: str) -> str:
    key = "did_you_mean" if len(questions) == 1 else "did_you_mean_pick"
    return message(key, language).format(options=suggest.phrase(questions))


async def _related(
    deps, turn, cfg, tenant, text, catalogue, language, prior=None
) -> list[str]:
    """'Did you mean ...?' for an unanswered question, only on a no-answer, English only.

    1. The college's own FAQ questions that share the question's key words, typos allowed
       (suggest.related_questions), from the turn's own search when it asked exactly this
       (`prior`: no extra search), else from one search without the widget's programme prefix.
    2. Failing that, for a college that uses Gemini, the question with its typos and misheard
       words fixed ("hostle fee for gilrs" -> "hostel fee for girls"); suggest.corrected rejects
       anything that is not a correction. The fix runs alongside the search, so the slower of the
       two sets the wait, and it is never sent when a FAQ question was already found."""
    if language != "en":
        return []
    start = time.monotonic()
    bare = suggest.strip_focus(text, catalogue.get("items", []))
    model = policy.gemini_model(cfg.get("model_policy"), deps.settings)
    reused = prior is not None and prior[0].strip() == bare.strip()
    spell = (
        asyncio.create_task(_spelling(deps, turn, model, bare))
        if model and not reused
        else None
    )
    try:
        items: list[dict] = []
        if reused:
            items = prior[1]
        else:
            try:
                items = (await deps.knowledge.search(tenant, bare, top_k=8)).get(
                    "items", []
                )
            except UpstreamError as exc:
                turn.span("related_questions", start, error=str(exc))
        generic = {w.lower() for w in cfg["college"].split()} | {
            w.lower() for a in cfg.get("college_aliases", []) for w in a.split()
        }
        found = suggest.related_questions(bare, items, generic)
        fixed = None
        if not found and model:
            fixed = await (spell or _spelling(deps, turn, model, bare))
            found = [fixed] if fixed else []
        turn.span(
            "related_questions",
            start,
            query=bare,
            reused=reused,
            corrected=bool(fixed),
            found=found,
        )
        return found
    finally:
        if spell is not None and not spell.done():
            spell.cancel()  # a FAQ question was found: the fix is not needed


async def _spelling(deps, turn, model: str, question: str) -> str | None:
    """The question with typos and misheard words fixed by Gemini, or None (suggest.corrected)."""
    start = time.monotonic()
    try:
        reply = await deps.llm.complete(
            model,
            [
                {"role": "system", "content": SPELLING_PROMPT},
                {"role": "user", "content": question},
            ],
            80,
            deps.settings.llm_timeout_s,
        )
    except (UpstreamError, TimeoutError) as exc:
        turn.span("spelling", start, model=model, error=str(exc) or "timeout")
        return None
    return suggest.corrected(question, reply)


_DIGIT = re.compile(r"\d")
_MARKDOWN = re.compile(r"[*#`>]+")  # not "_": it would turn NO_ANSWER into a sentence


async def _general_answer(deps, turn, cfg, text, language, voice) -> list[str] | None:
    """A labelled general answer when the college's information has nothing (policy.py
    general_answers), or None to give the usual no-answer. Every sentence with a digit, a number
    or a promise is dropped, so nothing college-specific can be stated from here; the visitor is
    still offered a person. Personal details are masked before Gemini sees the question."""
    s = deps.settings
    model = policy.general_model(cfg.get("model_policy"), s)
    if model is None or language not in GENERAL_LANGUAGE:
        return None
    # Gemini writes it (a college that opted in): in Hindi or Kannada too, where it translates
    # this college's answers; no number or promise can pass the filter below in any language.
    if (
        language != "en"
        and language not in s.native_answer_languages
        and language not in s.gemini_translated_languages
    ):
        return None
    start = time.monotonic()
    college = cfg["college"]
    prompt = GENERAL_PROMPT.format(
        college=college,
        length="one or two short sentences, to be spoken aloud"
        if voice
        else "at most three sentences",
        language=GENERAL_LANGUAGE[language],
    )
    try:
        reply = await deps.llm.complete(
            model,
            [
                {"role": "system", "content": prompt},
                {"role": "user", "content": text},
            ],
            120 if voice else 220,
            s.llm_timeout_s,
        )
    except (UpstreamError, TimeoutError) as exc:
        turn.span("general_answer", start, model=model, error=str(exc) or "timeout")
        return None
    kept: list[str] = []
    dropped = 0
    if not _is_no_answer((reply or "").strip()):
        reply = _MARKDOWN.sub("", reply or "").strip()
        for sentence in split_sentences(reply, final=True)[0]:
            sentence = sentence.strip()
            if not sentence:
                continue
            if (
                _DIGIT.search(sentence)
                or numbers_in(sentence)
                or _PROMISE.search(sentence)
                or looks_like_no_answer(sentence)
            ):
                dropped += 1
                continue
            kept.append(sentence)
    kept = kept[: 2 if voice else 3]
    turn.span("general_answer", start, model=model, kept=len(kept), dropped=dropped)
    if not kept:
        return None
    turn.model = model
    return [
        message("general_label", language).format(college=college),
        *kept,
        message("general_offer", language).format(college=college),
    ]


VOICE_SWITCHED_SOURCES = 3
VOICE_GAP_POINTS = 3.0  # PRD F2.10: "more than 3 points below the chat model"


def _voice_on_answer_model(cfg: dict) -> bool:
    return bool((cfg.get("voice_quality") or {}).get("use_answer_model"))


def voice_decision(chat_accuracy: float, voice_accuracy: float) -> dict:
    gap = round((chat_accuracy - voice_accuracy) * 100, 2)
    return {"gap_points": gap, "use_answer_model": gap > VOICE_GAP_POINTS}


def _programme_names(catalogue: dict | None, state: dict) -> list[str]:
    codes = state.get("programs", [])
    names = {p["code"]: p["name"] for p in (catalogue or {}).get("items", [])}
    return [names.get(c, c) for c in codes]


def _current_year(catalogue: dict) -> str | None:
    """The academic year the catalogue is for (PRD 7.3 retrieval academic_year: current)."""
    years = [
        p.get("academic_year")
        for p in catalogue.get("items", [])
        if p.get("academic_year")
    ]
    return max(set(years), key=years.count) if years else None


CITE_AGAIN = (
    "That answer could not be used because a sentence had no source number. Answer again from the "
    f"sources only, ending every factual sentence with its source number like [1]. If the sources "
    f"do not contain the answer, reply {NO_ANSWER}."
)


def _is_no_answer(text: str) -> bool:
    """The model's NO_ANSWER signal, however a small model spells it ("No_answer.", "NO ANSWER")."""
    return NO_ANSWER.lower() in text[:40].lower().replace(" ", "_")


_LOCAL = re.compile(
    r"[\u0900-\u0DFF]"
)  # Indic scripts: fixed messages already in the language
_KN_DIGITS = str.maketrans("೦೧೨೩೪೫೬೭೮೯०१२३४५६७८९", "01234567890123456789")


# Every token with a digit in it: amounts (₹2,50,000), times (21:30), years (2027-28) and codes
# (COMEDK code E197). numbers_in reads amounts and dates; a code is a fact too.
_DIGIT_TOKEN = re.compile(r"[A-Za-z]*\d+(?:[.,:/-]\d+)*[A-Za-z]*")


def same_facts(source: str, translation: str) -> bool:
    """A translation keeps every number, date and code of its source exactly as written
    ("₹6,00,000", "2026-27", "E197", Kannada or Devanagari digits read as 0-9), and adds none.
    Compared as written, not as read: numbers_in reads "2026-27" as 2026, 27 and 2027 in English
    but not with a Kannada suffix ("2026-27ನೇ"), which rejected every correct translation of an
    answer with an academic year (live, 17 Nov 2026)."""
    t = translation.translate(_KN_DIGITS)
    return sorted(_DIGIT_TOKEN.findall(source)) == sorted(_DIGIT_TOKEN.findall(t))


# Letters a translation into Hindi or Kannada must not contain: any other Indic script, or Arabic
# (seen live: "बारेغ" in a Hindi answer). Latin stays allowed (B.Tech, COMEDK).
_FOREIGN = {
    "hi": re.compile(r"[؀-ۿঀ-෿]"),
    "kn": re.compile(r"[؀-ۿऀ-౿ഀ-෿]"),
}


def clean_script(translation: str, language: str) -> bool:
    pattern = _FOREIGN.get(language)
    return not (pattern and pattern.search(translation))


async def _english_question(
    deps, turn: Turn, cfg: dict, text: str, language: str
) -> str | None:
    """The visitor's Hindi or Kannada question in English, or None to keep it as written (a
    failed call, or a "translation" that is still Indic, too long or changed a number)."""
    s = deps.settings
    model = policy.translation_model(cfg.get("model_policy"), s, language, None)
    start = time.monotonic()
    try:
        out = await deps.llm.complete(
            model,
            [
                {"role": "system", "content": QUESTION_PROMPT},
                {"role": "user", "content": text},
            ],
            120,
            s.llm_timeout_s,
        )
    except (UpstreamError, TimeoutError) as exc:
        turn.span("understand", start, model=model, error=str(exc) or "timeout")
        return None
    out = _MARKDOWN.sub("", out or "").strip().strip("\"“”'")
    ok = (
        bool(out)
        and not _LOCAL.search(out)
        and len(out) <= 400
        and numbers_in(out) == numbers_in(text.translate(_KN_DIGITS))
    )
    turn.span("understand", start, model=model, ok=ok, english=out if ok else None)
    return out if ok else None


async def _translated(
    deps,
    turn: Turn,
    parts: list[str],
    language: str,
    model: str | None = None,
    sarvam_fallback: bool = False,
) -> list[str]:
    """The checked English sentences, translated in one call by `model` (policy.translation_model:
    Gemini for a college that opted in to it). Kept only if the translation holds exactly the
    same numbers and dates; otherwise the visitor is told so, in their language, and offered a
    person. Fixed messages already in the language (e.g. "not certain") are kept as they are,
    so a reply that is already in the language costs no call."""
    english = [p for p in parts if not _LOCAL.search(p)]
    native = [p for p in parts if _LOCAL.search(p)]
    if not english:
        return native
    start = time.monotonic()
    text = " ".join(english)
    marks = list(dict.fromkeys(_MARK.findall(text)))
    # Markers out, and no space left before the full stop ("per year [1]." -> "per year.").
    plain = re.sub(
        r"\s+([.,!?।])", r"\1", re.sub(r"\s+", " ", _MARK.sub("", text))
    ).strip()
    s = deps.settings
    out, ok, tries = "", False, 0
    # One more try when a translation comes back but fails the check (seen live: Gemini once
    # dropped the COMEDK code "E197"); a failed call (quota, timeout) is not tried again.
    while not ok and tries < 2:
        tries += 1
        try:
            out = await deps.llm.complete(
                model or turn.model or s.answer_model,
                [
                    {"role": "system", "content": TRANSLATE_PROMPT[language]},
                    {"role": "user", "content": plain},
                ],
                600,
                s.llm_timeout_s,
            )
        except (UpstreamError, TimeoutError):
            out = ""
            break
        out = _MARKDOWN.sub("", out or "").strip()
        ok = (
            bool(out)
            and bool(_LOCAL.search(out))
            and same_facts(plain, out)
            and clean_script(out, language)
        )
    if not ok and sarvam_fallback and s.sarvam_api_key:
        # Gemini could not translate (seen 19 Nov 2026: its free quota ran out and every Hindi
        # and Kannada answer became "could not answer reliably"). Sarvam translates the same
        # checked English text; the same checks decide whether it is shown.
        from . import sarvam as sarvam_api

        try:
            out = await asyncio.to_thread(
                sarvam_api.translate, s.sarvam_api_key, plain, language
            )
            out = _MARKDOWN.sub("", out or "").strip()
            ok = (
                bool(out)
                and bool(_LOCAL.search(out))
                and same_facts(plain, out)
                and clean_script(out, language)
            )
            model = "sarvam-translate:v1" if ok else model
        except Exception as exc:  # noqa: BLE001 - the visitor is told and offered a person
            log.warning("translate: Sarvam failed (%s)", type(exc).__name__)
    turn.span("translate", start, language=language, ok=ok, model=model, tries=tries)
    if not ok:
        turn.offer_handoff = True
        turn.handoff_reason = (
            turn.handoff_reason or "The answer could not be given in their language"
        )
        turn.outcome = "partial"
        return [message("not_translated", language), *native]
    cite = (" " + "".join(f"[{m}]" for m in marks)) if marks else ""
    return [out + cite, *native]


def _judge(turn, sentence, sources, relevance_text, generic):
    """('skip'|'ok'|'fail'|'irrelevant', released text). The first released sentence must also
    cite sources that are about the question (answer-level relevance)."""
    if not turn.released and looks_like_no_answer(_MARK.sub("", sentence)):
        # A refusal in the model's own words ("the sources do not mention ...") is NO_ANSWER.
        return "irrelevant", ""
    ok = _check(turn, sentence, sources)
    if ok == "":
        return "skip", ""
    if ok is None:
        return "fail", ""
    cited = set(turn.checks[-1]["cited"])  # after re-attribution by the checks
    if not turn.relevance_checked and not cited:
        return (
            "hold",
            ok,
        )  # e.g. "Yes." waits until a cited sentence shows what it is about
    if not turn.relevance_checked:
        by_n = {x.n: x for x in sources}
        # Titles and section headings count: "Documents required" is the topic of its chunk.
        texts = [
            " ".join(
                [
                    by_n[n].text,
                    by_n[n].citation["title"],
                    by_n[n].citation.get("heading_path") or "",
                ]
            )
            for n in cited
            if n in by_n
        ]
        if not relevant(relevance_text, texts, generic):
            turn.checks[-1]["ok"] = False
            turn.checks[-1]["reason"] = "not relevant to the question"
            return "irrelevant", ""
        turn.relevance_checked = True
    return "ok", ok


def _check(turn: Turn, sentence: str, sources: list[Source]) -> str | None:
    t0 = time.perf_counter()
    sentence = _LEADING_MARKS.sub("", _PLACEHOLDER_MARKS.sub("", sentence))
    if not sentence.strip():
        return ""
    if _PLEASANTRY.match(sentence.strip()):
        turn.checks.append(
            {
                "ok": True,
                "reason": "pleasantry dropped",
                "auto_cited": False,
                "cited": [],
                "unsupported": [],
                "us": int((time.perf_counter() - t0) * 1e6),
                "sentence": sentence[:300],
            }
        )
        return ""
    r = check_sentence(sentence, sources)
    turn.checks.append(
        {
            "ok": r.ok,
            "reason": r.reason,
            "auto_cited": r.auto_cited,
            "cited": r.cited,
            "unsupported": r.unsupported,
            "us": int((time.perf_counter() - t0) * 1e6),
            "sentence": sentence[:300],
        }
    )
    if r.reason == ELIGIBILITY_VERDICT:
        # Dropped, not a refusal of the whole answer: the rest still goes out, and a person is
        # offered to look at the visitor's own case (roadmap item 1).
        turn.offer_handoff = True
        turn.handoff_reason = "Asked about their own eligibility: a person should check"
        return ""
    return _renumber(r.text, turn) if r.ok else None


async def _with_timeout(stream, timeout: float):
    """Each chunk must arrive within `timeout` seconds (PRD F1: gateway timeout > 20 s). Uses
    asyncio.timeout so the HTTP stream is read and closed in the caller's task."""
    it = stream.__aiter__()
    try:
        while True:
            try:
                async with asyncio.timeout(timeout):
                    chunk = await it.__anext__()
            except StopAsyncIteration:
                return
            yield chunk
    finally:
        await stream.aclose()


def _confidence(turn: Turn, sources: list[Source]) -> float:
    if turn.outcome in ("no_answer", "error", "blocked", "refused"):
        return 0.1
    if turn.outcome == "handoff":
        return 0.0
    by_n = {x.n: x for x in sources}
    scores = []
    for orig in turn.numbering:
        src = by_n.get(orig)
        if src is None:
            continue
        if src.citation["source_type"] == "catalogue":
            scores.append(0.95)
        else:
            scores.append(float(src.citation.get("vector_score") or 0.5))
    base = sum(scores) / len(scores) if scores else 0.4
    auto = sum(1 for c in turn.checks if c["auto_cited"])
    base -= 0.05 * auto
    if turn.outcome == "partial":
        base -= 0.15
    return round(max(0.0, min(1.0, base)), 3)
