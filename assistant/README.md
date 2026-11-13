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

- **LiveKit voice worker** (WebRTC streaming, hands-free VAD, live partial captions): needs LiveKit
  on the lab servers and the livekit-agents package. Voice stays push-to-talk over HTTP until then.
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
