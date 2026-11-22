# Module B — Assistant Engine & Channels

**Owner:** @koushikkumar2004 · **Database schema:** `chat` · **PRD:** sections 7, 9.2–9.5, 21.2, Appendix C

**Mission:** turn a question on any channel into a fast, grounded, safe answer — or the right handoff.

## Scope (Level 1)

- **Model layer:** LiteLLM + Ollama configuration, model policies per tenant, latency budget (PRD 7.5) and benchmarks.
- **Conversation engine (F1.2–F1.7 engine side):** router with rules and 8B fallback, multi-intent (F3.1, F3.1a); Answer agent with Admission and Faculty role packs (7.1–7.3); sentence-buffered streaming with deterministic checks (F1.4, F1.4a); asynchronous grounding review (F1.4b); input guardrails and PII masking.
- **Channels:** WhatsApp text (F1.11, F1.12); browser voice pipeline — LiveKit worker, faster-whisper, Piper, VAD, barge-in, voice style, digit-by-digit confirmation, channel consistency (F2.1–F2.6, F2.9, F2.10).
- **Decisions:** handoff package (F3.3) and callback creation through Module C; conversational lead capture, minor detection and scoring rules (F5.1, F5.3 detection, F5.4, F5.6).
- **Comparison:** DeepSeek-R1-Distill vs Qwen/Llama on the held-out test set (accuracy, groundedness, latency, GPU memory).

## Contracts

- **Provides:** `/chat/{id}/messages` (stream), `/voice/token`, `/channels/whatsapp/webhook`, conversation events — see `contracts/assistant.openapi.yaml`.
- **Consumes:** Module A's `/kb/search` and `/catalogue/*`; Module C's `/handoffs`, `/consents`, `/leads`.
- **By week 2:** a mock chat stream so Module C can build the widget against it.

## Thesis angle

Hallucination control with deterministic sentence checks; local SLMs vs DeepSeek distilled models under a latency budget.

## Local prototype: running Module B

```bash
source local.env.sh                       # from the repository root (creates local.secrets.sh once)
cd assistant
python -m assistant_api.migrate           # schema chat, RLS role admitai_chat
python -m assistant_api.pack load ../packs/demo-college --tenant-id <uuid>   # agent settings
uvicorn assistant_api.main:app --host 127.0.0.1 --port 8150
python -m pytest -q                       # offline: fake Module A and fake model gateway
```

| Endpoint | Purpose (PRD) |
|---|---|
| `POST /chat/{conversation_id}/messages` | Visitor token (from Module C) or service key. SSE: `token` (one checked sentence), `handoff_requested`, `consent_required`, `lead_prompt`, `done` (message id, trace id, confidence, offer_handoff, sources, outcome, versions) |
| `GET /chat/{conversation_id}` | Transcript with sources, confidence and versions for the staff console (services; F7.2) |
| `GET /chat/{conversation_id}/last` | The newest answer in the visitor's own conversation (visitor token only). A turn runs to the end even if the visitor's connection drops mid-answer, so the widget reads the answer back instead of losing it (F1 error handling) |
| `GET /traces/{trace_id}` | Spans and timings of one turn (Langfuse stand-in) |
| `PUT/GET /tenant-settings` | Agent part of the Tenant Pack: persona, languages, sensitive topics, escalation, retrieval floors |

One turn (PRD 7.2), at most two model calls on the hot path:

1. **Input guardrail** (no model): injection and abuse patterns, PII masked in traces, language (script + Hinglish words), per-session rate limit.
2. **Router, rules first**: explicit "human" and the tenant's sensitive topics hand off without a model call; intent lexicons give admission vs faculty role packs, sub-questions are split (F3.1a), programs are matched by code or alias and set the faculty department. The small model is asked only when no rule matches.
3. **Retrieval**: catalogue facts first (verbatim fees and dates), then Module A hybrid search per sub-question; hits under the tenant's score floor (nomic 0.50, bge-m3 0.40) are dropped. Follow-ups ("what about its fees?") are searched together with the previous question.
4. **Answer**: one streamed call with the role pack(s). Each sentence is held until it passes the deterministic checks (`checks.py`, < 1 ms): numbers must appear in a cited source or the catalogue (2,50,000 = Rs 2.5 lakh = 250000), factual sentences need a citation whose source shares their words, promise phrases are blocked, and the first sentence must cite a source about the question. A failing sentence is replaced by "I'm not certain..." plus a handoff offer and generation stops.
5. **Lead rules**: minors (age under 18, Class 11-12) trigger `consent_required` for guardian verification and are never asked for their own contact; after two answered questions a scored `lead_prompt` is sent once.

Measured locally with `qwen2.5:3b` (the lab's Qwen2.5-14B is a configuration change: `ANSWER_MODEL`, `FALLBACK_MODEL`, `ROUTER_MODEL`, `LLM_BASE_URL`):

- 50 injected wrong numbers: 50 blocked (PRD 20 acceptance test, in the unit tests).
- RUAS, 72 questions end to end: 48 of 54 answerable answered (one more is a deliberate refund handoff), 16 of 18 must-refuse refused. Demo college, 24 questions: 19 of 19 answered, 5 of 5 refused.
- Prompt wording matters for small models: asking for `NO_ANSWER` up front refused 21 of 25 answerable English questions; stating it last as a rare case answered 20 of 25 with the same refusals.
- `qwen2.5:3b` writes unreliable Hindi (the checks blocked garbled text and one invented date), so Hindi and Kannada questions get an English answer with a note in the visitor's language. Set `NATIVE_ANSWER_LANGUAGES=en,hi` with the 14B model.
- First checked sentence about 2-4 s with a warm model on a laptop CPU (PRD target 5 s p95 on the lab GPUs).

WhatsApp, voice, the asynchronous grounding review and the calls to Module C are below; what is still missing is listed at the end ("Still open").

## Channels (Day 5)

### WhatsApp text (PRD F1.11, F1.12)

| Endpoint | Purpose |
|---|---|
| `GET /channels/whatsapp/webhook` | Meta's subscription check (`WHATSAPP_VERIFY_TOKEN`) |
| `POST /channels/whatsapp/webhook` | Inbound messages; `X-Hub-Signature-256` checked with `WHATSAPP_APP_SECRET`; 200 at once, answered in the background; Meta's retries de-duplicated by message id |

- The college's business number (`whatsapp.phone_number_id` in `pack.yaml`) selects the tenant.
- Same engine, knowledge and checks as the widget. Replies are plain text (no Markdown or tables) with numbered sources, at most 3 messages, and reply buttons *Helpful / Not helpful / Talk to a person*. Reactions and buttons become feedback in Module C.
- The number is personal data from the first message: Module B keeps only a salted hash (`CONTACT_SALT`). The first message records the WhatsApp opt-in in Module C. *Talk to a person* asks "May we contact you on this number? Reply YES or NO"; on YES, contact consent, the lead (with the number) and a callback carrying the handoff package are created in Module C. Minors are pointed to guardian consent instead.
- Without `WHATSAPP_TOKEN` replies go to `chat.wa_outbox` (local prototype and tests); with it they go to the Cloud API. Real use needs Meta business verification and an approved number (PRD 15).

### Voice (PRD F2)

| Endpoint | Purpose |
|---|---|
| `POST /voice/token` | Short-lived voice token for a visitor and conversation (the Level 1 LiveKit room token's place) |
| `POST /voice/turn` | One push-to-talk recording -> faster-whisper -> the same engine in voice style -> Piper; returns transcript, captioned answer with sources, WAV audio and timings |
| `POST /voice/speak` | Read an answer aloud |

- Voice style (F2.3): at most 3 short sentences, offer to send details on chat. Spoken numbers in Indian words (250000 -> "two lakh fifty thousand"); captions keep the digits. The college's program names and aliases are given to Whisper as vocabulary.
- Unclear audio: "Sorry, I didn't catch that"; after two misses, chat or callback is offered. At most 5 voice turns at once (PRD 10.2); a TTS failure still shows the text answer. Audio is never stored, only the transcript.
- Models (downloaded once into `.local/models`): Whisper `base` (multilingual) and Piper `en_US-lessac-medium`. Check each Piper voice's licence before commercial use (PRD 14.2).
- The widget records with `MediaRecorder` and sends audio only to Module B; the browser's own speech service (which sends audio to Google or Microsoft) is no longer used. Pressing the microphone while the answer is spoken stops it (barge-in).
- Measured on a laptop CPU: speech-to-text about 1.7 s, answer about 2.9 s, speech 1.3 s, 5.9 s in all; the PRD target (3.5 s p95) assumes the lab GPUs and streaming.

### Asynchronous grounding review (PRD F1.4b)

`python -m assistant_api.review run --tenant-id <uuid>` (or `POST /reviews/run`; `scripts/nightly_local.sh` runs it before Module A's analyst). Every answered message is checked off the hot path against the exact source text the model saw (now kept with each citation). Each factual sentence must pass two tests: the model copies the source words that state the claim (or NONE) and code verifies the quote is in the source and about the claim; and no two key words of the claim may be missing from its sources. Unsupported answers (`GET /reviews/unsupported`) become *unsupported claim* proposals in Module A's approval inbox. With `qwen2.5:3b`: 5 of 5 test claims judged correctly, including Day 4's real hallucination ("M.Tech fees can be paid in instalments").

## Completing Module B (October 2026)

### Sentence checks bind each number to its fact (F1.4a)

A number must now be **the same fact** in its source, not merely appear somewhere in it. Labels
next to the number on both sides are compared by kind: fee component (tuition, hostel, application
...), category (general, management, NRI ...), intake vs duration, date event, and programme.
Dates match on day and month, and on their own label ("Written test and interview: 5 June").
Blocks only when both sides name a label of the same kind and they differ; unlabelled numbers are
checked for presence, as before.

- Measured on the demo-college catalogue (`tests/test_fact_binding.py`): every true fact in four
  phrasings passes; 151 of 151 swaps of real values (another component, category, programme, date
  event, month) are blocked. Before, all of them passed. Checks take under 1 ms per sentence.
- The "50 of 50 wrong numbers" test only ever injected numbers absent from every source; swaps of
  real values are what a small model gets wrong, and they are now tested.
- Catalogue lines name the component ("Hostel fee (general category): 90000 INR per year.").
- The alias `ME` (Mechanical) no longer matches the word "me" ("tell me", "call me back").

### Model gateway (PRD 9.5, 7.5)

- `gateway/litellm.config.yaml`: the LiteLLM proxy for the lab (proposed for `deploy/`). It maps the
  PRD's model names to the shared Ollama only, and has no provider keys and no LiteLLM retries:
  Module B does the PRD's one fallback itself.
- On the lab: `LLM_BASE_URL=http://127.0.0.1:8300/v1`, `LLM_API_KEY=<LiteLLM key>`,
  `ANSWER_MODEL=local/qwen2.5-14b-instruct`, `FALLBACK_MODEL` and `ROUTER_MODEL=local/llama3.1-8b`,
  `MODEL_ALIASES` mapping each name to itself.
- Each attempt's span in the trace has `attempt` and `ttft_ms` (time to first token). Connecting
  times out after 3 s, so a gateway that is down still leaves time for the fallback; reads time out
  after 20 s.
- In production the service refuses to start with a gateway outside the lab network (Level 1: no
  external model calls).

### Module A adapter (PRD 21.4) and mocks

- `assistant_api/knowledge.py` is the only code that talks to Module A, on the agreed contract:
  `POST /kb/search` (query, role, department, academic_year, top_k) and
  `GET /catalogue/programs/{id}/facts`.
- It also needs the programme list, `GET /catalogue/programs`, to recognise "CSE" in a question.
  That endpoint is in the proposed contract and must be agreed with Module A's owner. Without it,
  answers come from search alone and the trace says so.
- Replies are normalised in one place, so a separately built Module A plugs in by URL.
  [MODULE-A-HANDOFF.md](MODULE-A-HANDOFF.md) lists exactly what B sends and expects.
- The timeout is 5 s (`KNOWLEDGE_TIMEOUT_S`); a request that never reached Module A is retried once.
- `mocks/knowledge.py`: a contract-compatible Module A stand-in serving
  `tests/fixtures/demo_college.json` (synthetic, generated from `packs/demo-college`). It respects
  tenants and the service key.
- `mocks/llm_gateway.py`: an extractive stand-in for the model gateway (OpenAI API, streaming). It
  copies the best-matching source sentence, or says NO_ANSWER. It is not a model: it tests the
  pipeline, not answer quality.

### Conversation summary (F1.3) and office hours (F3.1)

- **Summary:** the prompt carries a summary of the earlier questions and the programmes discussed
  (personal details masked), then the last three exchanges word for word. There is no extra model
  call. The summary is stored in `chat.conversations.summary`, which erasure blanks.
- **Office hours:** read from the pack's `office_hours` ("Mon-Sat 09:00-17:00") and `timezone`
  (default Asia/Kolkata). Outside office hours a handoff is still a callback, and the visitor is
  told when the team is back. `handoff_requested.office_hours` = {open, hours, next_open, timezone}.

### WhatsApp additions (F1.11, F5.3)

- **STOP / UNSUBSCRIBE:** withdraws the WhatsApp and marketing consents in Module C. Writing again
  is a new opt-in, and nothing is ever sent proactively to someone who opted out.
- **One message at a time per person, in order.** Before this, a STOP sent while the previous
  answer was being written was lost. The lock covers one process; several workers would need a
  PostgreSQL advisory lock.
- **`POST /channels/whatsapp/notify`** (Module C only; staff replies and callback messages): plain
  text inside the 24-hour window, otherwise the college's approved template from
  `pack.yaml whatsapp.templates.<kind>`, or 409. Kinds: `callback_ack`, `callback_reminder`,
  `staff_reply`. Module C does not call it yet; its staff-reply and acknowledgement code is where it
  would.
- **`POST /channels/whatsapp/guardian-code`** (Module C only) and migration 006: the code waits
  sealed with a key derived from the guardian's number, which is never stored. It is sent only when
  that number writes to the college; expired codes are removed nightly.
  - Module C's `/consents/guardian/start` with `channel: whatsapp` now uses it instead of answering
    503. No widget change was needed.

### Voice (F2.1, F2.10)

- `POST /voice/token` also returns `livekit: {url, room, token}` when `LIVEKIT_URL`,
  `LIVEKIT_API_KEY` and `LIVEKIT_API_SECRET` are set. This is LiveKit's HS256 room token: one room
  per conversation, with the tenant and conversation in signed metadata. The push-to-talk `token`
  is unchanged, so the widget works as before.
- `POST /voice/eval-result` (Module A only): chat vs voice accuracy on the college's test set. More
  than 3 points worse moves voice answers to the answer model, with at most 3 sources. The decision
  is Module B's own setting (`voice_quality`) and survives Tenant Pack reloads.

### Engine

Database, Module C and Cloud API calls run in worker threads (`asyncio.to_thread`): a slow query
no longer stalls every other visitor's stream (`tests/test_concurrency.py`).

### Environment variables added

| Variable | Default | Purpose |
|---|---|---|
| `KNOWLEDGE_TIMEOUT_S` | `5` | Module A request timeout |
| `LIVEKIT_URL`, `LIVEKIT_API_KEY`, `LIVEKIT_API_SECRET` | unset | LiveKit room tokens (all three or none) |

Production now also requires `CONTACT_SALT` (16+ characters) and `WHATSAPP_APP_SECRET` whenever
WhatsApp is configured, and a lab-network `LLM_BASE_URL`.

### Run and test

```bash
cd assistant
python -m pytest -q                                   # 242 tests; DB tests need CHAT_TEST_*_DATABASE_URL
uvicorn mocks.knowledge:app --port 8151               # Module A stand-in  (KNOWLEDGE_API_URL=http://127.0.0.1:8151)
uvicorn mocks.llm_gateway:app --port 8301             # model stand-in     (LLM_BASE_URL=http://127.0.0.1:8301/v1)
python scripts/e2e_live.py                            # 20 live checks with the real A and C running (from the repo root)
```

### Still open (Level 1)

- ~~LiveKit voice worker~~: built 13 Nov 2026, see "Phases 2-5" below. The widget's LiveKit client
  is Module C's to add (contract-proposal/CONTRACT-PROPOSAL.md).
- **LangGraph:** the turn is an explicit pipeline with the PRD's steps. Moving it to a LangGraph
  graph is a structural choice for the team and guide; it changes no behaviour.
- **Redis/queues:** none in the repository. Background work is nightly jobs plus FastAPI background
  tasks. The per-person WhatsApp order now holds across workers (a PostgreSQL advisory lock, 13 Nov
  2026); the rate limits are still per process.
- **Module C calling `/channels/whatsapp/notify`** for WhatsApp staff replies (F3.6) and
  acknowledgements (F3.4); approved templates need Meta business verification.
- **Real-model measurements:** answer quality, latency on the lab GPU, and Hindi answers on the 14B
  model (`NATIVE_ANSWER_LANGUAGES=en,hi`).

### One more try for a missing source number (F1.4, F1.4a)

When the first sentence of an answer fails only because it has no source number, and nothing has
been shown yet, the engine asks the same model once more to answer from the sources with a source
number on every factual sentence (`engine.CITE_AGAIN`). The new answer goes through every check
again, so nothing unverified is released. There is only one extra try, and none for voice, where
the wait would be felt. This fixes refusals such as "Is there a hostel for first-year students?"
asked with the visitor's age, which was refused only because the opening "Yes, …" had no `[1]`.

### Fees shared by every programme are said once

`context.fee_summary` now starts with fees that are the same for every programme, for example
"The hostel fee (general) is 90000 INR per year for every program.", so "What is the hostel fee?"
is no longer answered with one programme as the example.

### Load for Module A's gate

`GET /load` (services) returns `live_turns`: the visitor answers this process is writing right now,
on any channel. Turns in `eval-…` conversations (the gate's own) are not counted. Module A's
evaluation gate waits between test questions while this is above zero.

### Tokens per answer, and WhatsApp formatting

- The model client asks the gateway for usage (`stream_options.include_usage`); the tokens of every
  call in a turn are summed through a context variable and saved on the answer
  (`chat.messages.prompt_tokens`, `completion_tokens`, migration 008). `/analytics/turns` reports
  them per model. The final usage chunk has no choices; it is not an error.
- `whatsapp.split_messages` keeps the line breaks between parts, so the source list stays on its
  own lines. An answer ending "[1]." once had "Sources:" joined to it with a space.
- `TOKEN_PRICES_INR` (optional): the platform's price per 1,000 tokens, per gateway model name, as
  `model=input:output,...` (for example `qwen2.5:3b=0.02:0.06`). Local models have no bill per
  token, so the figure is the platform's own, from GPU time. `/analytics/turns` then adds
  `cost_inr` per model. Bad entries are skipped.

### Spoken end-of-chat rating

`POST /voice/turn` with `expect=rating` reads a 1-5 rating from the recording ("four", "4 stars",
"char", "चार", "४"; `spoken_lead.spoken_rating`) and answers with thanks, or asks again when it is
unclear. It never calls the model, and it returns `rating` for the widget to save.

### Catalogue questions without the model (direct.py)

A plain fee or duration question about one programme, in English ("What is the hostel fee for
B.Tech CSE?", "How long is the M.Tech course?"), is answered from the catalogue entry itself, with
its citation and the values exactly as stored, and still passes the sentence check; the model is
not called. With several categories, the general one is given and the answer says the others
differ. Anything needing judgement (scholarships, refunds, instalments, what a fee includes, a
named category or quota, comparisons, two things at once, Hindi for now) goes to the model as
before. The trace shows `direct: "catalogue"` and the message has no model.

The one extra try for a missing source number (above) is skipped when the router already called
the model, so a turn never makes more than two model calls (PRD 7.5).

### No eligibility verdicts about the visitor (roadmap item 1)

A sentence telling the visitor that they themselves are or are not eligible, or can or cannot
apply ("As you are in class 11, you are not eligible"), is never released, cited or not
(`checks.VERDICT_PATTERNS`, English and Hindi). Stating the published criteria, or a condition
("you are eligible if you have 60% in PCM"), is allowed. The sentence is dropped rather than
refusing the whole answer, and a person is offered to look at the visitor's case. The prompt
(`answer-v5`) also tells the model to state criteria instead of verdicts. The demo college's test
set has the case as a critical question (`d26`).

### Kannada (added 9 Nov 2026)

Kannada is detected from its script and can be chosen in the widget. Fixed replies, WhatsApp
messages, Yes/No ("ಹೌದು", "ಇಲ್ಲ"), spoken times, routing keywords, the promise and eligibility
checks and spoken numbers have Kannada forms. Answers stay in English with a Kannada note until
the answer model writes Kannada reliably (`NATIVE_ANSWER_LANGUAGES=en,kn` on the lab model, after a
Kannada test run). Voice replies stay in English, because the speech model has no Kannada voice.
The Kannada wording needs review by a native speaker before real use.

### Kannada voice and answers (9 Nov 2026)

- **Speech:** when the visitor chose Kannada (or Hindi), the speech model is told the language
  instead of guessing, and a Kannada transcript is treated as Kannada. Whisper `base` is weak at
  Kannada; `WHISPER_SIZE=small` or larger (on the lab GPUs, `medium` or `large-v3`) is much better.
- **Spoken replies:** the speech synthesiser has an English voice only, so Kannada replies are
  shown as text with no audio, rather than read out wrongly (`voice.SPOKEN_LANGUAGES`).
- **Written Kannada answers were tried and switched off.** On the 3B model they were garbled, and
  one gave the hostel fee as the B.Tech fee: the fact check binds a number to its label only in
  English (roadmap 22). Answers stay in English with a Kannada note until that is fixed.
- **Kannada answers from templates (built instead).** Fee, duration and admission-date questions in
  Kannada are answered from fixed Kannada templates filled with the catalogue's exact numbers
  (`direct.py`), so the model never writes Kannada for them. Fees every programme shares ("the
  hostel fee") and admission dates are now answered from the catalogue in English too. Programme
  names in Kannada script come from the catalogue's aliases (demo college: `ಬಿ.ಟೆಕ್ ಸಿಎಸ್‌ಇ`,
  `ಎಂ.ಟೆಕ್`, `ಎಂಬಿಎ`, ...). Other Kannada questions get English answers with a Kannada note.
- **Kannada only for Kannada visitors (9 Nov 2026).** Kannada is recognised in Kannada script, in
  English letters ("B.Tech fees eshtu?", "first year ge hostel ideya?") and mixed with English, or
  when the visitor chose Kannada. Every reply is then in Kannada (`SAME_LANGUAGE_ONLY=kn`): fees,
  course length and admission dates from the Kannada templates; any other question gets a Kannada
  message offering the admissions team, never an English answer. The model is not asked then.
- **Translation is built but off.** `TRANSLATED_ANSWER_LANGUAGES=kn` translates the checked
  English answer and keeps it only if every number and date survives. On the 3B model the
  translations kept the numbers but lost the meaning, so turn it on only on a model that passes a
  Kannada test read by a native speaker.
- **Kannada replies at once.** A Kannada-only turn ends in a catalogue answer or the hand-off
  message, so the model router and the document search are skipped: 0.02-0.05 s instead of 7.9 s.
- **Misheard Kannada speech is asked again.** Told "Kannada", Whisper `base` once wrote the words
  in Urdu script. A transcript with non-Latin letters but none in the chosen language's script is
  treated as not heard, and the visitor is asked to repeat (in Kannada). The real fix is a larger
  speech model (`WHISPER_SIZE=small` or more; `large-v3` on the lab GPUs).
- **Kannada in English letters from speech.** Whisper `base` often writes spoken Kannada in English
  letters ("Arji Salislu Kone Yadinaam Ki Yavudhu"). The routing, the Kannada detection and the
  catalogue templates recognise the common questions written that way (arji, kone, dinanka,
  shulka, avadhi), and a template answer is not rejected by the word-overlap relevance test, since
  how it was chosen is its relevance. A real Kannada voice turn now answers in about 2 s.
- **Whisper `small` was tried and dropped:** 68 s per question inside Docker on the Mac, and no
  better on Kannada. Kannada voice needs a model trained on Indian languages on the lab GPUs
  (roadmap 24).

### Faster answers (10 Nov 2026)

Measured on the live demo, first words before → after: 4.3-10.2 s → 2.2-3.4 s for questions the
model answers; about 2 s → about 0.1 s for catalogue questions.

- **The models stay loaded** (`MODEL_KEEP_ALIVE`, default 30 minutes; Module A's search model the
  same). Ollama unloads a model 5 minutes after its last use, and the first question after a
  pause waited 8.3 s for it to load again.
- **A catalogue answer does not wait for the document search** (1-3 s): the catalogue facts are
  fetched first, and the search runs only if no catalogue answer is possible.
- **The reranker stays** (about 0.5 s of each search): answer accuracy is the weakest area, and it
  puts the most relevant passages first.

### The chosen language decides every reply (10 Nov 2026)

> Changed on 16 Nov 2026: a Hindi or Kannada question is now answered in its own language, and
> the visitor can ask for a reply language in the conversation. See "Languages: English, Hindi
> and Kannada" below.

- **EN, हिं or ಕ chosen in the widget always wins** (`guard.detect_language`): the reply is in that
  language whatever the question is typed or spoken in; the widget no longer switches by itself.
  Without a choice (WhatsApp), the language is still recognised from the text.
- **Hindi works like Kannada** (`SAME_LANGUAGE_ONLY=hi,kn`): fees, course length and admission dates
  from Hindi templates with the catalogue's numbers; other questions get a Hindi message offering
  the admissions team. The 3B model's own Hindi was garbled. Hindi and Kannada replies are shown
  as text: the speech synthesiser has an English voice only.
- **Voice listens for the chosen language**, English included; speech heard in another script
  is asked again in the chosen language. The speech model's output is capped
  (`max_new_tokens=80`, no previous-text conditioning), which stopped a 14.8 s loop, and the speech
  models load when the service starts (`VOICE_PRELOAD=1` in the demo): the first spoken question
  after a restart took 13.2 s, now 2.2 s.

## Phase 1 (13 Nov 2026)

### Lead score rules per college (F5.4)

The PRD fixes the scale (0-100) and the bands (hot / warm / cold) but not the weights or the
cut-offs. The values used so far are now defaults (`leads.DEFAULT_SCORING`), and a college can set
its own in `pack.yaml`:

```yaml
lead_scoring:            # every key optional; the defaults are shown
  engagement_per_turn: 6
  engagement_max: 30
  intent_per_signal: 10  # fees, dates, process, eligibility, documents, scholarship
  asked_human_bonus: 15
  intent_max: 45
  program_fit: 25
  bands: { hot: 70, warm: 40 }
  prompt_after_answers: 2   # answered questions before the lead prompt
```

Values that could break the scale are refused when the pack is loaded and by `PUT /tenant-settings`
(422): parts adding up to more than 100, `hot` not above `warm`, unknown keys, negative numbers.
Minor detection is unchanged (stated age under 18, or Class 11-12, F5.3).

### WhatsApp order across workers

One person's messages are still handled one at a time and in order, now also with several worker
processes or servers: besides the in-process lock, each message takes a PostgreSQL advisory lock for
that person (`whatsapp.person_lock_key`, on its own short connection, so a long answer does not hold
a pooled one). A lock held for more than 60 s (`SHARED_LOCK_WAIT_S`) is taken to be a stuck worker:
the message is then handled anyway and logged, rather than lost. Tested with a lock held by a
second connection (`test_another_worker_handling_the_same_person_is_waited_for`).

### Speech-to-text: Whisper sizes on the CPU (F2.2, PRD 7.5)

`python scripts/stt_benchmark.py --sizes base,small --out benchmarks/stt-<date>.json` transcribes
through `voice.Speech`, exactly as `/voice/turn` does (CPU, int8, VAD filter, vocabulary prompt).
Results of 13 Nov 2026 on the lab server (80 cores, 4-6 used per transcription), in
`benchmarks/stt-2026-11-13.json`:

| Model | English WER | English p50 | English + noise (10 dB) WER | Hindi WER (Hindi chosen) | Hindi p50 |
|---|---|---|---|---|---|
| `base` | 6.2% | 0.91 s | 6.3% | 107% (unusable) | 0.45 s |
| `small` | 2.6% | 2.32 s | 3.8% | 45% | 1.39 s |

- **The test audio is synthetic** (Piper voices), so these word error rates are a lower bound. Two
  earlier runs on fresh audio gave English 8-12% (`base`) and 5-7% (`small`); 20 questions is a
  small sample. The PRD acceptance test (50 recorded questions, <= 10% WER) still needs real
  recordings.
- `base` cannot write Hindi: it switched to Urdu, Greek or Latin script and looped. `small` writes
  Devanagari; most of its errors are spellings ("होस्तल" for "हॉस्टल"), which word error rate
  counts in full.
- Silence (3 s) gives an empty transcript with both.
- Code-mixed speech was only approximated (the English voice reading Hinglish); those numbers are
  not meaningful and are kept in the file for completeness.

**So English stays on `base` and Hindi or Kannada speech uses `small`** (`WHISPER_SIZE_INDIC`,
default `small`). `small` for every question would cost about 1.4 s more per English turn on the
CPU, against a 3.5 s p95 voice budget (F2.5). The larger model is used when the visitor chose हिं or
ಕ, or when no language was chosen and `base` detects another language (the clip is then heard again
by `small` in that language). Both models load at start-up with `VOICE_PRELOAD=1`.

**Voices:** Piper has Hindi voices (three) but none for Kannada (nor Tamil), so Kannada answers
cannot be spoken with Piper; Hindi speech output would need a Hindi voice whose licence is
confirmed first (the model card of `hi_IN-priyamvada-medium` names no licence; PRD 14.2).

| Variable | Default | Purpose |
|---|---|---|
| `WHISPER_SIZE_INDIC` | `small` | Whisper model for Hindi/Kannada speech (set equal to `WHISPER_SIZE` for one model) |

### Contract proposal

`contracts/assistant.openapi.yaml` is still the three-path stub. `contract-proposal/` holds v0.3
generated from this code (28 operations, with schemas) and a one-page summary for the guide,
including `GET /catalogue/programs`, which Module B needs from Module A's contract.

## Phases 2-5 (13 Nov 2026): LiveKit voice, audio consent, gateway, benchmarks

Everything below was run on the lab server (80 CPU cores, shared Ollama), with a local LiveKit
server and LiteLLM gateway bound to 127.0.0.1 in Module B's ports. All numbers are measured, from
the files in `benchmarks/`; test audio is synthetic (Piper), so speech accuracy is a best case.

### LiveKit voice worker (F2.1, F2.2, F2.4, F2.5, 9.3)

`python -m assistant_api.voice_worker` joins each conversation's room (found through LiveKit's
RoomService) and runs: Silero VAD (`vad.py`, CPU, ~0.1 ms per 32 ms window) -> live captions while
the visitor speaks -> faster-whisper -> the same turn code as push-to-talk (`voice_turns.py`:
misheard audio, spoken rating, digit-by-digit numbers, engine) -> Piper, sentence by sentence.

- **Hands-free and push-to-talk:** VAD by default; `{"type": "mode", "mode": "ptt"}` switches to the
  widget's button. Speech starts after 96 ms of speech and ends after 300 ms of silence (PRD 7.5).
- **Barge-in:** speaking over the assistant drops the queued audio frame by frame. The answer still
  finishes in the background and is saved, as in chat when the visitor disconnects, so the
  transcript stays whole; nothing more of it is spoken.
- **Who the room serves** comes only from the participant metadata Module B signed into the room
  token (tenant, conversation, session, language, audio consent).
- Messages between worker and widget: see CONTRACT-PROPOSAL.md ("LiveKit voice").

Measured end to end (`scripts/voice_e2e.py`: a scripted visitor in a real room, over WebRTC;
`benchmarks/voice-e2e-2026-11-13.json`):

| | Result (final run) | PRD target |
|---|---|---|
| Barge-in: visitor starts talking -> answer audio stops | 235, 238, 238, 245, 272 ms | <= 300 ms (F2.4) |
| End of speech -> first answer audio, p50 / p95 | 3.8 s / 4.3 s | <= 3.5 s p95 (F2.5) |
| Speech-to-text (base, CPU int8), per question | 0.55-0.70 s | 0.4 s on GPU (7.5) |
| Questions heard right (10, English) | 9 ("COMEDK" as "CommEDK", still answered) | |

Over all four runs that day, 11 of 13 barge-ins stopped within 300 ms (the two others: 313 and
329 ms). The worker itself stops in 96 ms (three VAD windows); the rest is audio already in the
visitor's WebRTC playout buffer, which varies from run to run.

The 3.5 s target is missed on this server. Per turn (traces): VAD 0.3 s + STT 0.6 s + **Module A
search ~1.45 s (budget 0.35 s)** + first sentence of the 8B model ~1 s + Piper ~0.1 s. Module B
already speaks the first sentence as soon as it passes the checks; the largest step is Module A's.

Three fixes came out of these runs:
- the final transcript no longer waits behind a live caption (two Whisper workers);
- the visitor's language travels in the token, so nothing waits for language detection;
- **the programme list is left out of the speech-to-text hint when it is long** (more than 400
  characters). On RUAS (213 programmes, cut to fit) Whisper heard 5 of 10 questions right and wrote
  programme names nobody said ("LL M", "Oral Implantology"); with the base vocabulary only, 9 of 10.
  This applies to push-to-talk as well.

### Model comparison (PRD 20, thesis)

Module A's harness (`knowledge_api.comparison`) answered RUAS's full test set (373 cases) through
Module B in a sandbox copy of the college, once per answer model; the router stays Llama 8B.
`benchmarks/model-comparison-2026-11-13.json`:

| Answer model (Q4_K_M) | Accuracy | Grounded | Refusals right | Routing | First sentence p50 / p95 | Answer p50 / p95 | GPU memory |
|---|---|---|---|---|---|---|---|
| Llama 3.1 8B Instruct | 55.8% | 66.1% | 92.3% | 93.9% | 2.6 / 3.2 s | 3.0 / 4.7 s | 5.9 GB |
| Qwen2.5-Coder 14B Instruct | 57.5% | 78.3% | 92.3% | 93.9% | 3.3 / 4.5 s | 3.6 / 7.3 s | 10.4 GB |

- The 14B model is **12 points more grounded** for 1.7 points of accuracy, at +0.7 s to the first
  sentence and +2.6 s at p95: grounding is where the larger model pays.
- Refusals and routing are equal, as they should be: the deterministic checks and the rules router
  do not depend on the answer model.
- **Not the PRD's models.** Qwen2.5-Coder is a code model, the only 14B one on the shared Ollama;
  Qwen2.5-14B-Instruct and the DeepSeek-R1 distills are not installed. Rerun with
  `python -m knowledge_api.comparison --tenant-id <uuid> --models local/qwen2.5-14b-instruct,local/llama3.1-8b,local/deepseek-r1-distill-qwen-14b`
  once the guide adds them. One Qwen-Coder answer (0.3%) came from the fallback model.

### Chat latency per step (PRD 7.5)

`scripts/latency_report.py`, 25 English questions, one at a time, on a quiet server
(`benchmarks/latency-2026-11-13.json`):

| Step | p50 | p95 | Budget |
|---|---|---|---|
| Input guardrail | 0.1 ms | 0.1 ms | 50 ms |
| Rules router | 0.9 ms | 118 ms (first catalogue load) | 20 ms |
| Router model (3 of 25 turns) | 288 ms | 316 ms | 300 ms |
| Module A search | **1,446 ms** | **1,621 ms** | 350 ms |
| Answer generation (8B) | 1,443 ms | 2,558 ms | |
| **First sentence (visitor)** | **2.5 s** | **2.8 s** | 2.5 s p95 |
| **Full answer (visitor)** | **2.9 s** | **3.9 s** | 5 s p95 |

Module A's search is four times its budget and is the one step that moves the first sentence past
2.5 s; it is Module A's to fix, and needs raising with its owner.

### Consented recordings (F2.6), off by default

- The college turns it on in pack.yaml: `voice: {store_audio: true}`; `retention.audio_days` (1-365,
  default 30). Wrong values are refused at pack load and on `PUT /tenant-settings` (422).
- The visitor ticks a box; `POST /voice/token` with `audio_consent: true` records Module C's
  `audio_recording` consent first. No consent on record: nothing is stored.
- Each spoken question (push-to-talk and LiveKit) goes to `chat.voice_audio` (migration 009, RLS),
  linked to the conversation, with its transcript and an expiry. Deleted by the nightly retention
  sweep, by an erasure request, and by `POST /privacy/erase` with `scope: "audio"` (withdrawn audio
  consent). Answers are generated speech and are not kept.

### Model gateway: LiteLLM on the lab server

All model calls now go through LiteLLM 1.106.3 (`127.0.0.1:8190`, its own virtualenv; local config
in `.local/litellm.lab.yaml`, not in the repository), in front of the shared Ollama. Module B's code
did not change: it already spoke the OpenAI API. Checked: streaming, token usage, `keep_alive` passed
through to Ollama, and requests without the key refused. `local/qwen2.5-coder-14b` was added to
`gateway/litellm.config.yaml` for comparison runs only: it is the only 14B model on the shared
Ollama today. `local/qwen2.5-14b-instruct` is still mapped to Llama 8B in `MODEL_ALIASES`, so traces
name the model that really answered.

### WhatsApp, simulated end to end (F1.11, F1.12)

`scripts/whatsapp_e2e.py` posts signed Meta-style webhooks to the running service, with a random
fictional sender (only its salted hash is stored), and reads the replies Module B captured in
`chat.wa_outbox` (no `WHATSAPP_TOKEN`, so nothing is sent). 11/11 checks pass
(`benchmarks/whatsapp-e2e-2026-11-13.json`): verification (right and wrong token), unsigned and
badly signed posts refused, empty payloads ignored, acknowledgement in 0.035 s, answer in 3.6 s,
duplicate deliveries ignored, two quick messages answered in order, STOP. Real Meta delivery still
needs a test number from the guide.

### Safeguards in this workflow (checked 13 Nov 2026)

- **Only the conversation's own visitor gets its room.** `POST /voice/token` refuses (403) a
  conversation started by another session: a room token is enough to hear answers and captions.
  The worker also listens to, and obeys, only the participant whose metadata Module B signed.
- **Withdrawal wins at once.** Withdrawing audio consent or an erasure request records a withdrawal
  (`chat.audio_withdrawals`, migration 010); a recording is refused in the same statement that would
  store it, even on a voice token issued before. A new consent lifts it.
- **No consent, no session, no college setting: no recording.** All three are checked when saving.
- **Voice tokens are rate-limited** per IP and per college, like chat (PRD 12.3).
- **Test traffic is never a visitor.** The live scripts mark their conversations `operator`
  (excluded from analytics and learning signals) and erase them in Modules B and C afterwards.
- **Capacity:** push-to-talk and the LiveKit worker have 5 slots each (`LIVEKIT_MAX_SESSIONS` for the
  worker). PRD 10.2 caps Level 1 voice at 5 sessions in all: set the worker to the share the server
  can carry. A room the worker turns away as busy is retried after 30 s, not at every poll.
- **Retention needs its nightly job:** `python -m assistant_api.privacy retention` now also deletes
  expired recordings. The lab deployment must schedule it (deploy/ is the guide's).
- **Everything binds to 127.0.0.1.** LiveKit's TCP fallback is off, because its listener cannot be
  limited to one address; WebRTC uses UDP 8172 on loopback.

### Run

```bash
source local.env.sh                       # plus .local/lab.env.sh on the lab server
scripts/run_local.sh                      # also starts LiveKit, LiteLLM and the voice worker when set up
cd assistant
python -m pytest -q                       # 343 tests
python scripts/voice_e2e.py --widget-key <key> --out benchmarks/voice-e2e-<date>.json
python scripts/whatsapp_e2e.py --tenant-id <uuid> --out benchmarks/whatsapp-e2e-<date>.json
python scripts/latency_report.py --widget-key <key> --tenant-id <uuid> \
    --questions ../packs/<college>/eval/questions.csv --out benchmarks/latency-<date>.json
```

New settings: `VAD_MODEL` (default `VOICE_MODEL_DIR/vad/silero_vad.onnx`, Silero VAD v5.1.2, MIT).
The worker needs `LIVEKIT_URL`, `LIVEKIT_API_KEY`, `LIVEKIT_API_SECRET` and the `livekit` package
(1.2.0, the realtime SDK: `livekit-agents` was not used because it needs PyAV 14+, and faster-whisper
1.1 needs PyAV below 14).

## Voice through Retell AI

Retell AI can be the voice layer: Retell does speech-to-text, turn-taking and text-to-speech, and
every answer still comes from this engine, through Retell's custom-LLM WebSocket
(`/voice/retell/llm/{call_id}`, `retell.py`, `routes/retell.py`). Answers are therefore checked,
cited and logged as in chat. Only calls created by `/voice/token` are answered.

1. In the Retell dashboard, create an agent with **Custom LLM** as its response engine and set its
   WebSocket URL to `wss://<public address of Module B>/voice/retell/llm` (Retell adds the call id).
   Locally, a tunnel gives that public address, e.g. `cloudflared tunnel --url http://127.0.0.1:8150`.
2. Set `RETELL_API_KEY` and `RETELL_AGENT_ID` (locally in `local.secrets.sh`) and restart Module B.
3. The widget's microphone then starts a hands-free Retell call (`retell-client-js-sdk` 3.0.2)
   with its own call panel; only "End call" ends it. Without these settings, push-to-talk works
   as before. After a tunnel restart, `python assistant/scripts/retell_agent.py --public-url …`
   points the agent at the new address.
4. **Languages (16 Nov 2026).** `retell_agent.py` (and so `scripts/voice_tunnel.sh`) now sets the
   agent up for English, Hindi and Kannada in one call (`--languages en,hi,kn`, the default):
   Retell's multi-locale `language`, Soniox speech recognition and a voice that speaks all three.
   `--languages en` restores the English-only agent. See "Languages" below.

## Answers written by Gemini (Google AI Studio), per college

A college can opt in to Gemini for writing answers: `model_policy: { …, external: gemini }` in
its `pack.yaml` (RUAS does). This is the PRD's Level 2 option, so it is per college and off by
default. Only the model that writes the answer changes: retrieval, the sentence-by-sentence checks
against the sources, citations and handoffs are the same.

- **What leaves the platform:** the question and the passages used to answer it, with e-mail
  addresses, phone and ID numbers masked first (`clients.RoutingLLM`). The router and the nightly
  grounding review stay on the local model.
- **Fallback:** the college's local model answers when Gemini fails: no first word within
  `GEMINI_FIRST_TOKEN_S` (6 s, Google overloaded), HTTP 429 (free quota used up), or any error.
  A citation retry that fails also falls through to the local model.
- **Setup:** create a key at aistudio.google.com (project "Default Gemini Project"), add
  `export GEMINI_API_KEY="…"` to `local.secrets.sh`, load the pack
  (`python -m assistant_api.pack load ../packs/ruas --tenant-id <id>`) and restart Module B.
  Loading a pack that opts in is refused when the platform has no key.
- **Model:** `GEMINI_MODEL` (default `gemini-3.1-flash-lite`) and `GEMINI_REASONING_EFFORT`
  (default `low`; lite models refuse `none`). On 17 Nov 2026 `gemini-3.5-flash-lite` had slowed
  to 8-9 s to the first word and `3.1-flash-lite` took 1.6 s; five RUAS questions went from a
  median 11.5 s to 3.8 s. On a free key `gemini-3.5-flash` ran out of daily quota after a few
  dozen questions. Speeds change on Google's side: compare with `scripts/check_gemini.py`.
- **Backup request (`GEMINI_HEDGE_S`, default 4 s):** a one-shot Gemini call (translation,
  understanding, routing) with no reply by then is sent again and the first reply wins. Seen live:
  the same Kannada translation took 37 s once and about 4 s otherwise; Kannada answers went from
  24.6 s to 8-9 s. Local models are never duplicated.

### Labelled general answers (`model_policy.general_answers`)

A college that uses Gemini can also opt in to general answers: `model_policy: { …, external:
gemini, general_answers: true }`. When its own information has nothing on a question (the turn
would be a no-answer), related questions are tried first: the college's own FAQ questions close
to it, then the spelling fix ("Did you mean …?" below). Only when neither gives a suggestion does
one short Gemini call (`engine._general_answer`) write a general answer, e.g. what COMEDK UGET is
and where to check it, on chat, voice (Retell, LiveKit, push-to-talk) and WhatsApp alike.

- It is shown under a label: "<College>'s official information doesn't cover this, so here is a
  general answer. Please confirm it with the admissions team." and ends with the usual offer of a
  person. The turn stays `no_answer`, so it still counts as a knowledge gap for Module A.
- Every sentence with a digit, a number word or a promise is dropped (deterministic, like the
  sentence checks), so a fee, date, code, cut-off or seat count can never come from it. The
  prompt also forbids college-specific facts; a reply of `NO_ANSWER`, a Gemini error or nothing
  left after the filter gives the usual no-answer message.
- English, and Hindi where Hindi answers are on; Kannada keeps the usual message.
- Like the spelling fix, it is an exception to the two-calls-per-turn budget (PRD 7.5), and only
  on a no-answer. Off by default: Level 1 answers only from the college's sources.

## "Did you mean …?" (`suggest.py`)

When a question was probably misheard or mistyped, the assistant offers what it thinks was meant
and the visitor picks. The same on every channel, with no model call except the Gemini spelling
fix below.

### Programme names

Speech-to-text and typing mangle programme names ("Nero Space Engineering" for "Aerospace
Engineering", "mechatronix"). When a question names no programme, its words are compared with
each programme's distinctive words (the name without "B. Tech. in" and the like); a close match
(similarity ≥ 0.8) is offered back as the corrected question, at no model cost (5-70 ms for
RUAS's 81 programmes on a laptop):

> Did you mean “What is the eligibility of B. Tech. in Aerospace Engineering?”? Say yes, or ask
> your question another way.

- A degree the visitor said picks among equally close names (B. Tech. over M. Tech.); otherwise
  up to two are offered. Ordinary questions (hostel, documents, scholarships) get no suggestion,
  and a fragment of a name ("technology" in "biotechnology") is not treated as a mishearing.
- A single match whose distinctive words were said exactly, only written differently ("B.Des
  Fashion Design" for "B.Des. Fashion Design"), is used without asking.
- Messages in English, Hindi and Kannada.

### Related questions on a no-answer

When the college's information has no answer (a typo such as "comdk" for "COMEDK", a vague or
misheard question), the college's own FAQ questions closest to it are offered instead
(`suggest.related_questions`, `engine._related`), before any general answer:

> **Visitor:** what is the comdk code
>
> **Assistant:** Did you mean one of these? 1. “What is the COMEDK code for Demo College?” 2.
> “What is the KCET code for Demo College?” Say the number, or ask your question another way.

- The questions come from Module A's search (the agreed contract): a curated answer reading
  "Q: …? A: …", or a page title or section heading that is itself a question. Only hits with a
  search score ≥ 0.55 count.
- A question is offered when it shares at least half of the visitor's key words (coverage
  ≥ 0.5), a close spelling counting ("comdk" ~ "comedk", "hostle" ~ "hostel"). Key words leave
  out question words, filler such as "details" or "course", and the college's own name; weak
  words ("allowed", "available", "admission", "students" …) count only when nothing else is
  left, so "is ragging allowed" does not match "Is fee payment allowed in instalments?".
- At most three, best first: most key words shared, then the higher search score, so "yes" picks
  the full match (COMEDK code) before a related one (KCET code).
- **Spelling fix:** for a college that uses Gemini (`external: gemini`, with or without general
  answers), when no FAQ question matches, one short Gemini call (`SPELLING_PROMPT`; only the
  question leaves the platform, personal details masked) fixes typos and misheard words, and the
  corrected question is offered the same way ("is there a hostle for gilrs" -> “Is there a hostel
  for girls?”). `suggest.corrected` accepts only a real correction (different words, still close
  to what was typed); a rewrite, an answer or an error leads on to the general answer or the
  usual no-answer.
- **Speed:** only on a no-answer, so an answered question costs nothing extra. The turn's own
  search is reused when it was one search for exactly this question; otherwise one search runs
  for the question without the widget's `<Programme name>: ` prefix (`suggest.strip_focus`), and
  a Gemini college's spelling fix starts alongside it, so the wait is at most the slower of the
  two, not their sum. The fix is cancelled as soon as a FAQ question is found, and with reused
  hits it is sent only when they hold none. Without Gemini there is no model call.
- English only: in Hindi and Kannada a no-answer goes straight on to the general answer or the
  usual no-answer. A suggestion the visitor picked that still has no answer gets no further
  suggestion.

### Picking a suggestion

- One suggestion is quoted, several are numbered. "Yes" (also "haan", "ಹೌದು", "हाँ") asks the
  first; "2", "two", "the second one" or "option 2" asks that one; "no" asks the visitor to put
  the question another way; anything else is a new question. The visitor's own words are what
  the transcript keeps.
- The turn's outcome is `clarify` (migration 011), not `no_answer`, so it is not a knowledge gap
  for Module A.

## Languages: English, Hindi and Kannada (16 Nov 2026)

One engine for every channel. The language a visitor writes or speaks in (the *input language*)
is kept apart from the language of the reply (the *response language*). Preferences are kept in
the conversation, so a switch halfway through loses nothing said before. English questions are
answered in the language the visitor picked; Hindi and Kannada questions in their own language;
"reply in Kannada" or "हिंदी में बताइए" switches until asked again. Code-mixed Kannada-English and
Hindi-English (in either script) count as Kannada or Hindi.

### How a turn works

| Step | Where | Model call |
|---|---|---|
| Detect the input language: script, Hinglish / Kannada words in English letters, code-mixing | `language.detect` | none |
| Requests in the message: a language ("Kannada please", "ಕನ್ನಡದಲ್ಲಿ ಹೇಳಿ", "Hindi mein batao") or an output ("text only", "reply with voice", "both text and voice") | `language.preferences` | none |
| Decide the reply language (rules below) and remember the preferences in the conversation state | `language.decide` | none |
| A Hindi or Kannada question, at a college that opted in to Gemini: put into English, alongside the catalogue request | `engine._english_question` | 1 (Gemini) |
| Route, search, answer **in English**, every sentence checked as before | engine | 1 |
| Translate the checked English answer in one call; kept only if every number, date and code survives (`engine.same_facts`), else one more try, else "I couldn't give a reliable answer in your language" and a person offered | `engine._translated` | 1 (Gemini) |
| Catalogue templates (fees, course length, dates) and fixed messages are already written in Hindi and Kannada: no translation | `direct.py`, `prompts.MESSAGES` | none |

The reply's language, in order: (1) asked for in the conversation, until asked again; (2) the
question's own Hindi or Kannada; (3) for a short message with nothing to tell by ("yes", "2",
"B.Tech fees?"), the previous reply's language; (4) the widget's pick (EN / हिं / ಕ) for English
questions (a *new* pick replaces an earlier request); (5) English. "Kannada please" right after an
answer asks the same question again in Kannada. Questions about a language ("Is the course taught
in English?", "Is Kannada compulsory?") are not requests.

**Per college.** A college that opted in to Gemini (`model_policy.external: gemini`, e.g. RUAS)
gets natural Hindi and Kannada answers to every question (`GEMINI_TRANSLATED_LANGUAGES`). A Level 1
college (local models only) keeps its earlier behaviour: catalogue templates in Hindi and
Kannada, the admissions team for everything else, and no external call.

**Model calls (PRD 7.5).** English: as before. A reply to be translated skips the model router,
so it stays at two calls (answer + translation). A Hindi or Kannada *question* at a Gemini college
is three (question + answer + translation): the one exception to the two-call budget, taken
because a Kannada query missed the page an English one found (RUAS COMEDK code, live,
16 Nov 2026). `TRANSLATE_QUESTIONS=0` turns it off. English replies are never translated.

### Channels

| Channel | Hears | Answers | Speaks |
|---|---|---|---|
| Web chat | text | engine | widget: `POST /voice/speak {"text", "language"}` |
| Push-to-talk (`POST /voice/turn`) | Sarvam Saaras (detects the language) or local Whisper | engine; `response_language` for English speech | Piper (English), Sarvam Bulbul (Hindi, Kannada); `output=text` makes no audio |
| Retell calls (phone / web) | Retell with Soniox (English, Hindi, Kannada, code-switching) | engine, per utterance, through the custom-LLM WebSocket; "one moment" in the language being spoken | Retell's multilingual voice (`11labs-Monika`, `eleven_v3`: one voice for the whole call) |
| WhatsApp text | text | engine | a voice note only when asked ("reply with voice") |
| WhatsApp voice notes | Meta media, then Sarvam or Whisper ("You said: ..." shown first) | engine | a voice note back by default (MP3 from Sarvam, or Piper's audio as OGG/Opus), plus the text with sources and buttons |

`speech.py` chooses the engine per language: `SPEECH_TO_TEXT_PROVIDER` and
`TEXT_TO_SPEECH_PROVIDER` = `auto` (default: Sarvam hears every recording when it has a key;
English is spoken by Piper, Hindi and Kannada by Sarvam), `local` or `sarvam`. If Sarvam fails
(down, quota, over 30 s of audio), Whisper hears it instead; an English reply falls back to Piper;
a Hindi or Kannada reply is shown as text, never read out by the English voice. Audio is never
stored (the WhatsApp outbox keeps only the size and a hash of an audio reply).

### Which speech provider, and why

Checked against the providers' own documentation on 16 Nov 2026:

- **Retell (calls): extended, not replaced.** Its agent `language` takes a list of locales
  (`en-IN`, `hi-IN`, `kn-IN`); speech recognition for Kannada is offered only by Soniox and Azure,
  and Soniox also follows code-switching; ElevenLabs' `eleven_multilingual_v2` and
  `eleven_flash_v2_5` have no Kannada, OpenAI's voices do. So the existing agent keeps its
  custom-LLM engine and gets the three locales, Soniox and `11labs-Monika` (`eleven_v3`).
- **Sarvam AI (push-to-talk, WhatsApp voice notes): added.** Retell only handles live calls: it
  cannot transcribe a WhatsApp voice note or voice a push-to-talk reply. Locally, Whisper `base` is
  weak at Kannada (benchmarks/stt-2026-11-13.json) and Piper has no Hindi or Kannada voice. Saaras
  `saaras:v4` (speech-to-text, language detection, code-mixing, OGG/Opus input) and Bulbul
  `bulbul:v4-flash` (Hindi, Kannada and English voices) fill exactly that gap (`sarvam.py`).
- **Bolna: not added.** It is a call platform, the role Retell already has; it would add a provider
  without an architectural gain.
- **No change of model.** Answers still come from the college's model chain (LiteLLM / Ollama,
  Gemini for opted-in colleges); this module has no LangGraph.

### Setup

1. **Hindi and Kannada answers**: a college opts in to Gemini as before ("Answers written by
   Gemini" above). Nothing else to set; `GEMINI_TRANSLATED_LANGUAGES=hi,kn` is the default.
2. **Sarvam voices and hearing**: create a key in the Sarvam dashboard, add
   `export SARVAM_API_KEY="..."` to `local.secrets.sh` (never to the repository) and restart
   Module B. Optional: `SARVAM_SPEAKERS`; `SARVAM_STT_MODE=transcribe` writes English words in the
   Indic script too (the default `codemix` keeps them in English letters, which the catalogue and
   the search need).
3. **Retell in three languages**: `scripts/voice_tunnel.sh` (or
   `python assistant/scripts/retell_agent.py --public-url https://... --languages en,hi,kn`). The
   dashboard shows the result; Soniox and the OpenAI voice are billed by Retell.
4. **WhatsApp voice notes**: need `WHATSAPP_TOKEN` (Cloud API) to download the audio and upload
   the reply. Without it a voice note gets "please type your question" in the visitor's language.

All settings are in `.env.example`. New ones:

| Variable | Default | Purpose |
|---|---|---|
| `GEMINI_TRANSLATED_LANGUAGES` | `hi,kn` | Gemini colleges: reply languages translated by Gemini |
| `TRANSLATE_QUESTIONS` | `1` | Gemini colleges: Hindi and Kannada questions put into English first |
| `SARVAM_API_KEY` | unset | Sarvam speech; unset = local speech only |
| `SARVAM_BASE_URL` | `https://api.sarvam.ai` | |
| `SARVAM_STT_MODEL`, `SARVAM_STT_MODE` | `saaras:v4`, `codemix` | speech-to-text model and mode |
| `SARVAM_TTS_MODEL`, `SARVAM_SPEAKERS` | `bulbul:v4-flash`, `en=simran_en_customer,hi=simran_hi_assistant,kn=chaitra_kn_conversation` | voices, one per language |
| `SARVAM_TIMEOUT_S`, `SARVAM_RETRIES` | `15`, `2` | per request; 429 and 5xx retried with backoff, 4xx not |
| `SPEECH_TO_TEXT_PROVIDER`, `TEXT_TO_SPEECH_PROVIDER` | `auto` | `auto`, `local` or `sarvam` (production refuses `sarvam` without a key) |
| `WHATSAPP_VOICE_REPLIES` | `1` | a voice note is answered with a voice note too |

API additions (all optional, old callers unchanged; `contract-proposal/CONTRACT-PROPOSAL.md` v0.5):
`done` adds `input_language`, `language_reason`, `code_mixed` and `output_mode`; `/voice/turn`
takes `response_language` and `output` and returns `providers`; `/voice/speak` takes `language`.
The widget changes this calls for (an output toggle and a play button) belong to Module C and are
written down in `contract-proposal/widget-multilingual.md`; the widget works unchanged meanwhile.

### Tests

```bash
python -m pytest -q tests/test_language.py tests/test_sarvam.py tests/test_retell_agent.py  # no database
python -m pytest -q tests/test_multilingual_db.py                                           # test database
```

- `test_language.py`: detection (scripts, romanised, code-mixed), requests and non-requests, the
  decision rules, and that translations keep every number, date and code.
- `test_sarvam.py`: Sarvam requests (fields, the key only in the header), retries on 429/503, no
  retry on 400-422, timeouts; the per-language choice of engine and every fallback; WAV to
  OGG/Opus.
- `test_multilingual_db.py`, end to end through the API: English to Kannada and to Hindi, Kannada
  to Kannada, Hindi to Hindi, code-mixed, a question understood in English, a translation that
  changes a number (never shown) or drops a fact (tried again), no translation for English, the
  Level 1 college unchanged, switching and back with the context kept, preference-only messages,
  push-to-talk in Kannada and Hindi with audio, `output=text`, `/voice/speak` per language, a Retell
  call switching Kannada to English, WhatsApp Hindi text, Kannada and English voice notes with
  voice replies, "text only", and a voice note that cannot be fetched.
- `test_retell_agent.py`: the agent settings for English only and for three languages.
- `conftest.py` now removes `RETELL_*`, `SARVAM_API_KEY`, `GEMINI_API_KEY` and `WHATSAPP_TOKEN`
  from the environment: one existing test had been creating a real Retell web call.

Whole suite on 16 Nov 2026: everything passes except the two LiveKit worker timing tests
(`test_barge_in_...`, `test_audio_starts_after_the_first_sentence_...`), which fail now and then
on this laptop with or without these changes (they count event-loop ticks).

### Measured (16 Nov 2026, live, RUAS on Gemini, laptop CPU)

`benchmarks/multilingual-2026-11-16.json`, median of 3 chat turns:

| Direction | Total | Question to English | Answer translation |
|---|---|---|---|
| English to English | 3.3 s | - | - |
| English to Kannada | 5.2 s | - | 1.3 s |
| English to Hindi | 4.2 s | - | 1.5 s |
| Kannada to Kannada | 7.0 s | 0.7 s | 2.1 s |
| Hindi to Hindi | 10.4 s | 1.2 s | 1.8 s |

Also verified live: a Retell web call created with the three-language agent and driven over the
public tunnel the way Retell does (Kannada to Kannada with the COMEDK code, Hindi to Hindi,
"English please" to English; first words after about 1.2 s); signed WhatsApp webhooks (Hindi and
Kannada answers in the outbox, a bad signature refused); push-to-talk with the local models
(English speech, English reply with audio; a Kannada reply as text, as there is no Kannada voice
without Sarvam).

**With the real Sarvam API** (16 Nov 2026): Saaras heard in 0.5-1.1 s and Bulbul spoke in about
1-1.5 s (`/voice/speak`, Kannada and Hindi, 1.4 s). Language detection was right every time
(probability 1.0).

| Push-to-talk (RUAS) | Heard | Reply | Voice | Total |
|---|---|---|---|---|
| Kannada | "R U A S-ನ COMEDK code ಯಾವುದು?" | Kannada, the code E197 | Sarvam | 12.6 s |
| Hindi | "क्या ERUIS में hostel है?" | Hindi | Sarvam | 11.6 s |
| Code-mixed | "B.Tech CSE fees ಎಷ್ಟು?" | Kannada, the B.Tech CSE fee | Sarvam | 10.7 s |
| English, Hindi replies picked | "What is the COMEDK code for RUAS?" | Hindi, E197 | Sarvam | 6.9 s |
| English | the same | English | Piper | 8.0 s |
| Kannada, the browser's `audio/webm;codecs=opus` | as Kannada above | Kannada, E197 | Sarvam | 12.8 s |

WhatsApp voice notes (OGG/Opus, demo college, real engine and Sarvam; only Meta's media store
stood in for): a code-mixed Kannada note was heard exactly and answered in Kannada text and a
Kannada voice note (MP3) in 5.3 s; a Hindi note in Hindi (5.8 s).

Found by these live runs and fixed: Sarvam refuses a content type with parameters, so WhatsApp's
`audio/ogg; codecs=opus` and the browser's `audio/webm;codecs=opus` are sent as `audio/ogg` and
`audio/webm` (`sarvam.clean_mime`); and `transcribe` mode wrote "B.Tech CSE" as "ಬಿಟೆಕ್ ಸಿಎಸ್ಇ"
and COMEDK as "ಕಾಮೆಂಟ್ಕೆ", hence `codemix` by default.

### Known limitations

- **Sarvam voices have not been judged by ear**: the API calls, language detection and audio
  formats are verified live, but no native speaker has yet listened to the Kannada
  (`chaitra_kn_conversation`) and Hindi (`simran_hi_assistant`) voices. The test questions were
  themselves spoken by Bulbul, which is easier to hear than a real caller on a phone.
- **Sarvam hears every recording, English too** (`SPEECH_TO_TEXT_PROVIDER=auto` with a key): the
  audio leaves the platform for Sarvam. Use `local` to keep English on Whisper, or record the
  college's opt-in as for Gemini before real use.
- **WhatsApp voice notes are not verified against Meta**: no Cloud API token here, so the media
  download and the audio upload ran only against the local outbox (Sarvam, the engine and the
  database were real).
- **Real speech on a Retell call is not verified**: Retell accepted the three-language settings and
  the answer path was driven over the tunnel with Kannada and Hindi text, but no one has spoken
  Kannada or Hindi on a call yet. Test with staff volunteers (PRD), code-switching included.
- **Translation quality**: `gemini-3.5-flash-lite` once dropped a code from a translation (now
  rejected and tried again) and mixes English words into Kannada ("ಅಡ್missions"). Have a native
  speaker read a sample; `GEMINI_MODEL=gemini-3.5-flash` writes better, at a smaller free quota.
  The fixed Kannada messages were written without a native speaker too.
- **Free Gemini quota**: a Hindi or Kannada turn uses up to three Gemini calls; rapid testing hit
  HTTP 429 and 503, and the local fallback model then timed out on the laptop (2 of 15 benchmark
  turns).
- **Translated replies arrive whole**, not sentence by sentence (the checked answer is translated
  in one call); voice says "one moment" meanwhile.
- **Level 1 colleges** (no Gemini) still answer Hindi and Kannada only from catalogue templates or
  hand over to a person; `TRANSLATED_ANSWER_LANGUAGES` turns translation on with a local model that
  has passed a native speaker's test.
- "Did you mean ...?" suggestions remain English only; romanised Hindi and Kannada are recognised
  by word lists, and replies to them are in the native script.
- Sarvam's real-time speech-to-text takes at most 30 s per request; longer voice notes fall back to
  Whisper. The LiveKit worker still uses the local models only.

## Phone calls through Vapi (17 Nov 2026)

Vapi is the phone line: it places and receives calls, hears the caller and speaks, in English,
with the college's saved Vapi assistant (for RUAS "Ruas Receptionist": voice Elliot, Soniox
speech recognition, greeting "Thank you for calling Ruas..."). The answers come from this engine,
so a phone answer is checked, cited and logged like a chat answer. Retell (web calls in the
widget) is a separate channel and is unchanged.

| Piece | Where | What it does |
|---|---|---|
| `POST /voice/vapi/calls` | routes/vapi.py | Module C (a callback after contact consent) or an operator places a call: `{"to": "+91..."}` -> Vapi `POST /call` with the saved assistant, `VAPI_PHONE_NUMBER_ID` and the per-call overrides |
| `POST /voice/vapi/webhook` | routes/vapi.py | Vapi's server messages: `assistant-request` for an inbound call (the number is mapped to a college; the reply is the saved assistant with the overrides), `status-update`, `end-of-call-report` |
| `POST /voice/vapi/chat/completions` | routes/vapi.py | Vapi's custom-LLM hook, every turn: the caller's last words go to the engine (channel `phone`, voice style, **English only**) and each checked sentence is streamed back as an OpenAI chunk, citation markers removed; "One moment, let me check that." when the answer takes over 1.2 s |
| per-call overrides | vapi.py | the model becomes this service (`provider: custom-llm`), metadata names the college and conversation, call events come to the webhook. Vapi documents that the saved assistant stays unchanged; voice, greeting and transcriber are the saved ones |

- **Who may use it.** A turn is answered only for a call this service placed or was asked about
  (`assistant-request`), or a request carrying `VAPI_SERVER_SECRET` (`X-Vapi-Secret` or Bearer);
  the webhook always needs the secret. Placing calls is for service keys (Module C's key may call
  only this endpoint of the voice ones; operators may call all).
- **Privacy.** The caller's number is used in flight only, never stored or logged; the
  conversation is `vapi-<call id>` (channel `phone`), so it appears in the transcript and
  analytics like any other.
- **English only.** Whatever the caller speaks, replies are English (`only_language` in
  `engine.handle_turn`); "Kannada please" gets "I'm sorry, on this line I can only speak
  English." A Hindi or Kannada question at a Gemini college is still understood (put into
  English for the search).
- `VAPI_ANSWERS=assistant` lets the saved assistant's own model answer instead (as set in the
  Vapi dashboard, without the college's sources or the checks).

### Setup

1. Keys in `local.secrets.sh` (git-ignored): `VAPI_API_KEY` (private key), `VAPI_ASSISTANT_ID`,
   `VAPI_SERVER_SECRET` (any 24+ random characters), `VAPI_TENANT_ID` (the college for numbers
   not mapped to one). Done on this laptop for RUAS.
2. **A phone number** in the Vapi dashboard. A free Vapi number is US-only and inbound-only;
   calling or receiving Indian numbers needs an imported Twilio, Vonage or Telnyx number. The
   Vapi account here has none yet, and Vapi asks for a card on file for pay-as-you-go use.
3. A public address for this service: the voice tunnel (`scripts/voice_tunnel.sh`) already gives
   one; start Module B with `VAPI_PUBLIC_URL=https://<tunnel>` (again after a tunnel restart).
4. `python assistant/scripts/vapi_setup.py connect --phone-number-id <id> --public-url
   https://<tunnel> --tenant-id <college>`: inbound calls on the number go through
   `/voice/vapi/webhook`, and the number is mapped to the college (`chat.channels`, migration 012);
   then set `VAPI_PHONE_NUMBER_ID` for outbound calls.
5. `python assistant/scripts/vapi_setup.py status` checks it all;
   `python assistant/scripts/vapi_setup.py call --to +91...` places a test call to your own number.

### Tests and what was verified (17 Nov 2026)

`tests/test_vapi.py` (14): the call request to Vapi (assistant, number, overrides, key only in the
header), number validation before any call, retries on 503, refusals without the key; the
overrides; a streamed turn with markers removed and channel `phone`; "one moment" for a slow
answer; unknown calls refused without the secret; English whatever the caller speaks; a
non-streaming request; an inbound `assistant-request` and its turns, with the caller's number
stored nowhere; an unmapped number and the webhook's secret; placing a call (service keys only,
international numbers only, 503 when not configured, 502 when Vapi refuses).

Live: the account and the saved assistant were read through Vapi's API; over the public tunnel,
an inbound `assistant-request` returned the assistant with the engine override, and the call's
turns were answered in English from RUAS's sources ("The COMEDK code for RUAS is E197", "one
moment" after about 1.5 s, the whole answer in 4.5-7 s); a request for an unknown call without
the secret got 401.

Not yet verified: a real phone call. The account has no phone number, and Vapi's Chat API (a
call-less way to test the assistant) asks for a card on file first. So Vapi accepting the
per-call model override, and the exact request Vapi sends to the custom-LLM hook (both written
from Vapi's documentation), are still to be seen on the first real call; `status-update` and
`end-of-call-report` are logged only. The voice-style prompt still offers "details on chat",
which reads oddly on a phone call (changing it changes the prompt version Module A's gate tracks).
