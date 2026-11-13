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
"""

from __future__ import annotations

import asyncio
import re
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from typing import AsyncIterator, Callable

from . import hours, policy, store, scope
from .auth import Caller
from .checks import (
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
from .leads import detect_minor, score_lead, scoring_rules, should_prompt
from .prompts import (
    AGENT_VERSION,
    ENGLISH_ONLY_NOTE,
    NO_ANSWER,
    PROMPT_VERSION,
    TRANSLATE_PROMPT,
    looks_like_no_answer,
    message,
    system_prompt,
    user_prompt,
)
from .router import ROUTER_PROMPT, Route, parse_model_route, route

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
) -> AsyncIterator[dict]:
    global _live_turns
    TOKEN_USAGE.set({"prompt": 0, "completion": 0})  # this turn's model usage (F9.4)
    live = not conversation_id.startswith(EVAL_PREFIX)
    if live:
        _live_turns += 1
    try:
        async for ev in _turn(
            deps, caller, conversation_id, text, requested_language, channel
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
) -> AsyncIterator[dict]:
    s = deps.settings
    turn = Turn()
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

    start = time.monotonic()
    g = check_input(text, requested_language)
    language = g.language
    turn.span("input_guardrail", start, ok=g.ok, reason=g.reason, language=language)
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
    else:
        # ---------------------------------------------------------- route
        start = time.monotonic()
        try:
            catalogue = await deps.knowledge.programs(tenant)
        except UpstreamError as exc:
            turn.span("catalogue_error", start, error=str(exc))
            catalogue = {"items": [], "general_dates": []}
        if catalogue.get("unavailable"):
            turn.span("catalogue_error", start, error=f"no {catalogue['unavailable']}")
        # The previous program carries over only into a genuine follow-up ("what about its fees?").
        previous = (
            [p for p in catalogue["items"] if p["code"] in state.get("programs", [])]
            if is_follow_up(text, history)
            else []
        )
        decision = route(
            text, language, cfg["sensitive_topics"], catalogue["items"], previous
        )
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

        if decision.handoff:
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
                and language not in s.translated_answer_languages
            )
            if (
                decision.decided_by == "default"
                and s.use_model_router
                and len(text.split()) >= 3
                and not catalogue_only
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
                if channel == "web_voice" and _voice_on_answer_model(cfg)
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
            try:
                for res in await asyncio.gather(*searches):
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
                if released:
                    turn.model = None
                    turn.released.append(released)
                    yield await say(released)
                    turn.span("answer", start, model=None, direct="catalogue")
                else:
                    # A Kannada visitor gets Kannada only: the English answer is checked as
                    # usual, held back, then translated; a translation that changes any number or
                    # date is never shown (roadmap 22: the model cannot yet write Kannada safely).
                    translate = (
                        language in s.translated_answer_languages
                        and answer_language != language
                    )
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
                            voice=channel == "web_voice",
                            summary=summary,
                        ):
                            if ev is not None:
                                yield ev
                    if translate:
                        for part in await _translated(deps, turn, held, language):
                            yield await say(part)
                        released = released or bool(held)
                if (
                    answer_language != language
                    and not released  # a catalogue answer is already in the visitor's language
                    and turn.outcome in ("answered", "partial")
                    and language in ENGLISH_ONLY_NOTE
                ):
                    yield await say(ENGLISH_ONLY_NOTE[language])
            if turn.outcome == "no_answer":
                turn.offer_handoff = True
                turn.handoff_reason = "No reliable source for the question"
                yield await say(message("no_answer", language))

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
        user_text=text,
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
                            if not noted:
                                noted = True
                                yield await say(message("not_certain", reply_language))
                            continue
                        # F1.4a: a single question stops at the first unverifiable sentence.
                        turn.outcome = "partial" if turn.released else "blocked"
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
            if turn.released or attempt == 1:
                turn.outcome = "error" if not turn.released else "partial"
                turn.offer_handoff = True
                turn.handoff_reason = "The assistant was unavailable"
                yield await say(message("unavailable", reply_language))
                return
        finally:
            if chunks is not None:
                await chunks.aclose()  # stop the model generating once we stop reading


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


async def _translated(deps, turn: Turn, parts: list[str], language: str) -> list[str]:
    """The checked English sentences, translated. Kept only if the translation holds exactly the
    same numbers and dates; otherwise the visitor is told so, in their language, and offered a
    person. Fixed messages already in the language (e.g. "not certain") are kept as they are."""
    english = [p for p in parts if not _LOCAL.search(p)]
    native = [p for p in parts if _LOCAL.search(p)]
    if not english:
        return native
    start = time.monotonic()
    text = " ".join(english)
    marks = list(dict.fromkeys(_MARK.findall(text)))
    plain = re.sub(r"\s+", " ", _MARK.sub("", text)).strip()
    s = deps.settings
    out = ""
    try:
        out = await deps.llm.complete(
            turn.model or s.answer_model,
            [
                {"role": "system", "content": TRANSLATE_PROMPT[language]},
                {"role": "user", "content": plain},
            ],
            600,
            s.llm_timeout_s,
        )
    except UpstreamError:
        pass
    out = (out or "").strip()
    ok = (
        bool(out)
        and bool(_LOCAL.search(out))
        and numbers_in(out.translate(_KN_DIGITS)) == numbers_in(plain)
    )
    turn.span("translate", start, language=language, ok=ok)
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
