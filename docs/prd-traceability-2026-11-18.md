# PRD v2.0 compliance review and traceability matrix (18 Nov 2026)

Scope: every functional requirement in PRD v2.0 (F1.1–F10.6) and the Level 1 non-functional targets
(section 16), checked against the code on `main` at 908511b and against the running system on the
local Mac. It replaces the matrix in `prd-gap-analysis.md` (9 Nov), which predates the website-built
knowledge base, Retell / Vapi / Sarvam / ElevenLabs voice, Gemini answers, the new staff roles and
self-learning.

A feature is marked done only if its behaviour was seen working: in the running system, in the
measurement below, or in a test that actually runs. A screen or a function existing is not enough.

## How it was verified

- **Live test-set run.** The RUAS test set (`packs/ruas/eval/questions.csv`, 72 cases) was sent
  through the running assistant (Module B, Gemini 3.1 Flash-Lite, free key) on 18 Nov. A case
  passes when the answer is given in the asked language and cites the expected curated answer;
  out-of-scope cases pass when the assistant declines or offers a person.
- **Readiness checklist.** RUAS readiness read from Module A (`GET /tenant-pack/readiness`).
- **Contracts.** Routes in code compared with `contracts/*.openapi.yaml`.
- **Code reading** of the paths named in each row, and the earlier test evidence where the tests
  still run.

### Live test-set results (RUAS, 72 cases)

| Kind | Passed | Gave a real answer | Note |
|---|---|---|---|
| English direct | 4 / 5 | 4 / 5 | "What is the application fee?" asks *Did you mean …?* although a curated answer exists |
| English paraphrase | 14 / 20 | 18 / 20 | 4 correct answers cite a web page instead of the curated answer |
| Kannada | 4 / 16 | 9 / 16 | most failures: "could not give this reliably in Kannada" (translation rejected) |
| Hindi | 1 / 10 | 5 / 10 | same; one Hindi answer contradicts the Kannada one (entrance exam needed?) |
| Hinglish | 1 / 3 | 2 / 3 | |
| Out of scope (critical) | 12 / 18 | — | intake, placements, hostels are now on the crawled website, so these cases are out of date |
| **All** | **36 / 72 (50%)** | | PRD target ≥ 90% factual accuracy, 0 critical failures |

First checked sentence: median 6.3 s, p95 23.9 s (Hindi median 16.2 s, Kannada 12.0 s). PRD L1
target: ≤ 5 s p95.

### Readiness checklist (Module A's own launch gate)

| Check | Result |
|---|---|
| Fees for every programme (2026-27) | **fails** — 9 PhD / MDS programmes |
| Important dates for every programme | **fails** — about 40 programmes |
| Eligibility for every programme | **fails** — 1 programme |
| Approved knowledge to answer from | passes — 102 documents, 214 curated answers |
| An owner for every document (17.1) | **fails** — most crawled pages |
| Current academic year | passes |
| No curated answer disagrees with the catalogue | passes |
| Launch test set (F10.3) | **fails** — 73 real questions, 150 needed |

## Status key

✅ Fully implemented (seen working) · 🟡 Partially implemented · ❌ Not implemented ·
⚠️ Implemented but incorrectly (or against the PRD) · 🧪 Implemented but not properly tested ·
❓ Requires clarification · — Level 2 only (not due for the pilot)

Priority: **P0** before showing to leadership or any real applicant · **P1** during the pilot ·
**P2** later.

## 1. Chat assistant (F1)

| ID | PRD requirement | Existing implementation | Files | Status | Gap | Recommended improvement | Priority |
|---|---|---|---|---|---|---|---|
| F1.1 | Widget via one script tag; responsive; < 1.5 s on 4G; full page at `/chat` | `embed.js` loads the Next.js widget in an iframe; `/chat` page exists | C `web/public/embed.js`, `web/src/app/widget`, `app/chat` | 🧪 | 4G load time never measured; iframe Next.js page, not the light component the PRD assumes | Measure with Lighthouse (4G throttling); trim the widget bundle if > 1.5 s | P1 |
| F1.2 | Sentence-buffered SSE; each sentence released only after checks; Markdown; numbered citations | Engine streams sentence by sentence through `_judge`; widget renders inline Markdown and `[n]` | B `engine.py` `_answer`, `checks.py`; C `Widget.tsx` | ✅ | Markdown tables not rendered | — | P2 |
| F1.3 | Last 10 turns + summary; follow-ups resolve pronouns | History of `HISTORY_TURNS` turns plus summary | B `context.py`, `store.history` | 🧪 | Pronoun follow-ups are tested only in DB tests, which CI skips | Add a CI database (see T1) | P1 |
| F1.4 | Fees, dates, eligibility, seats only from catalogue or cited chunks; else "not sure" + handoff | Catalogue direct answers; checks reject unsupported numbers; handoff offered | B `checks.py`, `direct.py`, `engine.py` | ⚠️ | Live run: 50% pass. Indian-language answers often fall back to "not sure"; one Hindi answer contradicted the Kannada one on a policy fact | Fix the Hindi/Kannada answer path (see F1.6); add the contradiction to the test set | **P0** |
| F1.4a | Per-sentence checks, no LLM, < 20 ms: numbers match, citation per factual sentence, banned promises | Deterministic checks with catalogue fact binding | B `checks.py` | ✅ | Timing (< 20 ms) not measured in CI | Add a timing assertion to the unit test | P2 |
| F1.4b | Asynchronous LLM grounding review of every answer | Nightly review job | B `review.py`, `POST /reviews/run` | 🧪 | Not scheduled on this Mac; no evidence it ran on current answers | Schedule it and show its last run in the console | P1 |
| F1.5 | Starter chips per tenant; quick replies after each answer | Chips from the pack; related questions after answers | C `Widget.tsx`; B `engine._related` | ✅ | Related questions add up to ~5 s to some turns (seen in traces) | Run them after the answer is sent (already async in part) | P2 |
| F1.6 | English and Hindi text, auto-detected, manual switch (L1); Kannada etc. in L2 | EN/हिं/ಕ picker; detection; Gemini translation with a same-facts check | B `language.py`, `engine._translated`; C `Widget.tsx` | ⚠️ | Hindi 5/10 and Kannada 9/16 real answers; Hindi first sentence median 16 s. Since 17 Nov a clear English question is answered in English whatever is picked, which narrows the PRD's "manual switch" | Answer Indian-language questions from the curated answer's own translation where one exists; cache translations of curated answers; confirm the picker rule with the guide | **P0** |
| F1.7 | "Talk to a human" always visible; starts handoff | Button in the widget header | C `Widget.tsx`, `routes/handoffs.py` | ✅ | — | — | — |
| F1.8 | Thumbs up/down on every answer with optional reason | Thumbs, reason picker, comment box (since 17 Nov) | C `Widget.tsx`, `lib/feedback.ts`, `routes/feedback.py` | ✅ | — | — | — |
| F1.9 | Rich cards | — | — | — | L2 | — | — |
| F1.10 | Marksheet upload for eligibility | — | — | — | L2 | — | — |
| F1.11 | WhatsApp Cloud API: same agents; 24-hour window; templates outside it; opt-in on first message | Complete webhook, window and template logic | B `whatsapp.py`, `routes/whatsapp.py` | 🧪 | No WhatsApp token or number configured; only tested with signed fake webhooks; no Meta-approved templates | Register a test number; get templates approved; one end-to-end test with a staff phone | P1 |
| F1.12 | WhatsApp formatting: no tables, short links, ≤ 3 messages, quick replies | `split_messages`, buttons | B `whatsapp.py` | 🧪 | Unit-tested only; never seen on a phone | As F1.11 | P1 |

## 2. Voice (F2)

| ID | PRD requirement | Existing implementation | Files | Status | Gap | Recommended improvement | Priority |
|---|---|---|---|---|---|---|---|
| F2.1 | Browser voice over WebRTC (LiveKit); push-to-talk and hands-free with VAD | Hands-free through **Retell AI** web calls; push-to-talk over HTTP; LiveKit tokens only if configured | B `routes/voice.py`, `retell.py`, `routes/retell.py`; C `lib/retell.ts`, `Widget.tsx` | ⚠️ | Works, but through a US-hosted service that receives raw audio, against the L1 privacy rule (no external model calls, data in India). Enabled for every college once a key is set; no per-college opt-in | Gate Retell / Sarvam / Vapi per college like Gemini (`model_policy.external`), off by default; record the decision and a DPA; keep local push-to-talk as the default | **P0** |
| F2.2 | Streaming STT (faster-whisper on GPU) with live captions | Soniox (via Retell), Sarvam saaras, or local Whisper `base`; live captions in the call panel | B `speech.py`, `sarvam.py`; C call panel | ⚠️ | Same privacy issue as F2.1; local Whisper path is CPU and slow | As F2.1; faster-whisper on the lab GPU for the local path | P0 |
| F2.3 | ≤ 3 sentences; offers "send details on chat?" for long answers | Voice answers shortened; offer sentence | B `voice_turns.py`, `engine.py` | ✅ | — | — | — |
| F2.4 | Piper TTS (local); Hindi best-effort; barge-in stops playback ≤ 300 ms | ElevenLabs `eleven_v3` (Retell) or Sarvam bulbul; Piper locally | B `retell_agent.py`, `sarvam.py`, `voice.py` | ⚠️ | External TTS (privacy); barge-in time never measured | As F2.1; measure barge-in on a recorded call | P1 |
| F2.5 | End of speech → first audio ≤ 3.5 s p95 | — | — | 🧪 | Never measured; chat first-sentence p95 is 23.9 s on the free Gemini key, so voice cannot meet 3.5 s reliably | Measure from Retell call logs; paid Gemini key or lab model; fixed filler within 1 s | **P0** |
| F2.6 | Transcript + consented audio stored, linked; retention (default 30 days) | Transcripts; audio with consent and withdrawal (migrations 009, 010) | B `audio_store.py`, migrations 009–010 | 🧪 | Retell calls: audio is held by Retell, outside the college's retention rule | Turn off Retell call recording / storage in the agent; document Retell's retention | P0 |
| F2.7 | Telephony (SIP), DTMF, warm transfer | Vapi inbound calls answered by the engine | B `vapi.py`, `routes/vapi.py` | — | L2; inbound only, no DTMF menu or warm transfer | Keep as L2 | — |
| F2.8 | WhatsApp voice notes; Indic neural TTS | Voice notes transcribed and answered | B `whatsapp.py` | — | L2; not connected (F1.11) | — | — |
| F2.9 | Spoken lead capture: phone digits read back before saving | Digit parsing in English/Hindi/Kannada, read-back, ten-minute expiry | B `spoken_lead.py` | ✅ | — | — | — |
| F2.10 | Same curated answers and facts on voice; voice test set; switch to 14B if > 3 points worse | Voice uses the same engine; A runs the voice suite | A `voice_eval.py`; B `POST /voice/eval-result` | 🧪 | No voice test run on the current voice providers | Run the voice suite once on Retell / Sarvam output | P1 |

## 3. Routing, role agents and handoff (F3)

| ID | PRD requirement | Existing implementation | Files | Status | Gap | Recommended improvement | Priority |
|---|---|---|---|---|---|---|---|
| F3.1 | Router: {intent, role, confidence} per sub-question, language, sensitive flag; rules first, 8B model only when inconclusive | Rules then model router | B `router.py`, `engine._model_route` | 🧪 | Acceptance needs ≥ 90% on a **200-question labelled routing set**; no such set exists | Build the routing set from the 73 RUAS questions plus staff questions; report routing accuracy | P1 |
| F3.1a | Multi-intent: both role packs, one paragraph each, hand off only the failing part | Implemented | B `engine.py` (multi-part handling) | 🧪 | DB tests only (skipped in CI) | T1 | P1 |
| F3.2 | Two queues: Admission and Faculty (by department) | Queues enforced by RLS; console inbox | C `routes/handoffs.py`, migrations | ✅ | New roles (counsellor, finance, hostel, management) are not mapped to queues | Decide which queue each new role sees | P2 |
| F3.3 | Handoff package: transcript, AI draft, sources, lead, language | Implemented | C `routes/handoffs.py`; B handoff event | ✅ | — | — | — |
| F3.4 | Callback-first; contact + preferred time with consent; task; acknowledgement by chat, WhatsApp or SMS with expected time | Callback task; e-mail and WhatsApp acknowledgement | C `routes/handoffs.py`, `messaging.py` | 🟡 | SMS not built; WhatsApp not connected | Add SMS through an Indian provider (DLT template) or drop SMS from the pilot scope | P2 |
| F3.5 | Live takeover | Handoff banner with staff name | C `HandoffBanner` | — | L2 | — | — |
| F3.6 | Staff reply by WhatsApp, e-mail or SMS; offered as a correction | E-mail and WhatsApp replies; correction offered | C `routes/handoffs.py`, `messaging.py` | 🟡 | SMS missing; WhatsApp not connected | As F3.4 | P2 |
| F3.7 | SLA timers, escalation, assignment | Overdue escalation exists | C `messaging.escalate_overdue` | — | L2 (partly done) | — | — |
| F3.8 | Co-pilot | — | — | — | L2 | — | — |

## 4. Knowledge base (F4)

| ID | PRD requirement | Existing implementation | Files | Status | Gap | Recommended improvement | Priority |
|---|---|---|---|---|---|---|---|
| F4.1 | Upload PDF, DOCX, PPTX, XLSX/CSV, HTML, TXT (≤ 50 MB); OCR for scans | All formats parsed; OCR through Tesseract | A `ingest.py`, `ocr.py` | 🧪 | Tesseract is not installed on this Mac, so OCR cannot run and its tests skip | Install Tesseract in the server image; one scanned-prospectus test | P1 |
| F4.2 | Website crawl: include/exclude, depth, robots.txt, manual re-crawl | Two crawlers: `crawl.py` (rules, depth) and `site_kb.py` (sitemap, robots, programme extraction, nightly) | A `crawl.py`, `site_kb.py`, `POST /kb/sources/{id}/sync` | ✅ | Crawled programme pages crowd out general topics in search ("Is there a hostel?" returned M.Sc. pages) | Tag page types (programme / facility / policy) and boost by intent | P1 |
| F4.3 | Catalogue CSV/XLSX: programme, department, level, duration, intake, eligibility, fees by category, dates, contact | Import and website extraction | A `catalogue.py`, `site_kb.py` | 🟡 | Readiness: fees missing for 9 programmes, dates for ~40, eligibility for 1 | Import the admissions office's fee and dates sheet; keep website extraction as a check | **P0** |
| F4.4 | Curated Q&A editor: variants, answer, owner, validity | Editor and API | A `curated.py`; C console knowledge page | ✅ | — | — | — |
| F4.5 | Metadata: year, tags, owner, valid_from/to, status | Columns and console | A `documents.py` | 🟡 | Most crawled pages have no owner (readiness fails) | Assign an owner role per page type automatically at crawl time | P1 |
| F4.6 | Only approved, in-date content retrievable; expiry automatic | `servable()` filter | A `search.py` | ✅ | — | — | — |
| F4.7 | Parse → clean → chunk ~400 tokens, 15% overlap → nomic-embed (768) → pgvector HNSW + full-text | Pipeline in place; hybrid search, RRF, reranker | A `ingest.py`, `search.py`, `rerank.py` | 🟡 | pgvector is not installed here: vectors are plain arrays searched in memory, no HNSW. Keyword search is English-only (`to_tsvector('english')`) | Install pgvector on the server; `simple` text config for Hindi/Kannada keywords | P1 |
| F4.8 | Re-upload = new version; diff; rollback | Versioning and rollback | A `documents.py`, `gate.py` | ✅ | — | — | — |
| F4.9 | Scheduled re-crawl, change detection, approval inbox | Nightly website re-build (`pack.yaml website:`) | A `site_kb.py` | — | L2, partly built early | — | — |
| F4.10 | Connectors | — | — | — | L2 | — | — |
| F4.11 | Contradiction detection | Curated answer vs catalogue check | A `health.py` | — | L2, partly built | — | — |

## 5. Leads and consent (F5) and campaigns (F6)

| ID | PRD requirement | Existing implementation | Files | Status | Gap | Recommended improvement | Priority |
|---|---|---|---|---|---|---|---|
| F5.1 | Conversational capture of name, phone, e-mail, city, programme, qualification, time | Contact flow in the widget; spoken capture | C `ContactFlow.tsx`, `routes/consents_leads.py` | ✅ | — | — | — |
| F5.2 | Consent with purpose and time; separate marketing opt-in | `app.consents` | C `routes/consents_leads.py` | ✅ | — | — | — |
| F5.3 | Minors: no own contact; guardian verified by code | Guardian code by e-mail; WhatsApp path | C, B `guardian codes` | 🟡 | E-mail works; WhatsApp path not connected | As F1.11 | P1 |
| F5.4 | Rule-based score 0–100, bands | Implemented | C `scoring.py` | ✅ | — | — | — |
| F5.5 | Lead list, pipeline, notes, CSV | Implemented | C console leads | ✅ | — | — | — |
| F5.6 | UTM / referrer on session start | Implemented | C `routes/widget` session | ✅ | — | — | — |
| F5.7–F5.8 | De-duplication, predictive score, CRM | Webhooks only | C `routes/webhooks.py` | — | L2 | — | — |
| F6.1–F6.6 | Campaigns | — | — | — | L2 | — | — |
| F6.7 | UTM, consent and marketing opt-in collected now | Implemented | C | ✅ | — | — | — |

## 6. Staff console (F7) and feedback (F8)

| ID | PRD requirement | Existing implementation | Files | Status | Gap | Recommended improvement | Priority |
|---|---|---|---|---|---|---|---|
| F7.1 | Handoff and callback queue by role and department with due times | Inbox | C `console/handoffs` | ✅ | — | — | — |
| F7.2 | Conversation view: transcript, sources, confidence, feedback, lead | Implemented | C `console/handoffs/[id]` | ✅ | — | — | — |
| F7.3 | "Correct this answer" → correction record | Implemented | C `routes/corrections` | ✅ | — | — | — |
| F7.4 | Review queue of low-rated answers by role | Implemented | C `console/review` | ✅ | — | — | — |
| F7.5 | Notifications: in-app, e-mail, browser push | All three (push since migration 013) | C `notifications`, `lib/push.ts`, `public/sw.js` | 🧪 | E-mail needs an SMTP server configured; push not tried on a phone | One staff test on a phone and an inbox | P1 |
| F7.6 | Mobile console; WhatsApp to staff | Responsive layout | C | — | L2 | — | — |
| F7.7 | Approval inbox ranked by impact; batch approve; weekly digest | Implemented; gate before approval | C `console/approvals`; A `gate.py` | ✅ | Gate runs used 7–8 cases only | Run the full suite in the gate (see F10.3) | P1 |
| F8.1 | Thumbs up/down; reason picker | Implemented, plus comment box | C | ✅ | — | — | — |
| F8.2 | End-of-conversation 1–5 rating; spoken "was this helpful?" | Chat rating; spoken rating (`expect=rating`) | C; B `voice_turns.py` | ✅ | Spoken rating not wired into Retell calls | Ask at the end of a Retell call | P2 |
| F8.3 | Implicit signals: rephrase, human, abandonment, hang-up, barge-in | `signal.logged` events | C `routes/signals.py`; A | 🟡 | Hang-up and barge-in come from the old voice path, not Retell or Vapi | Read them from Retell / Vapi call-ended events | P2 |
| F8.4 | Corrections and handoff replies as labelled data | Implemented | C, A `learning.py` | ✅ | — | — | — |
| F8.5 | Weekly gap and improvement report | Implemented; self-learning report since 17 Nov | A `health.py`, `learning.py`, `selflearn/report.py` | ✅ | Self-learning is on locally only; never run on real feedback | Run it on staff test feedback before the demo | P1 |

## 7. Analytics (F9) and onboarding (F10)

| ID | PRD requirement | Existing implementation | Files | Status | Gap | Recommended improvement | Priority |
|---|---|---|---|---|---|---|---|
| F9.1 | Overview: conversations, resolution, handoff, CSAT, leads, voice vs chat | Implemented with the 3.5 definitions | C `routes/analytics.py` | ✅ | Local data mixes test and demo conversations | Exclude `eval-` conversations everywhere (B already does in live counts) | P2 |
| F9.2 | Top intents, trending, unanswered | Implemented | B `/analytics/topics`; C | ✅ | — | — | — |
| F9.3 | Acceptance, eval score per release, proposals | Implemented | A `/reports/quality`; C | 🟡 | "Eval score per release" has no full-suite runs to show | F10.3 | P1 |
| F9.4 | Latency, GPU, model usage, API cost per tenant | Latency and model usage shown | B `/analytics/turns`; C | 🟡 | No GPU metrics; **no Gemini / Retell / Sarvam / Vapi cost** per tenant although these are now paid services | Record provider usage and cost per turn and call | P1 |
| F9.5 | Funnels, ROI, PDF reports | — | — | — | L2 | — | — |
| F10.1 | Tenant Pack with all listed contents | Implemented | A `tenant_pack.py`, `packs/*/pack.yaml` | ✅ | — | — | — |
| F10.2 | Validation with readiness checklist | Implemented and working | A `/tenant-pack/readiness` | ✅ | RUAS fails 5 of 7 blocking checks (see above) | Fix the data, not the checklist | **P0** |
| F10.3 | ≥ 150 real questions + generated cases; launch blocked until threshold passes | Gate and threshold in code | A `tenant_pack.py` (`150`), `catalogue_cases.py` | ❌ | RUAS has 73 real questions; the full set has never been run on the current system; several "out of scope" cases are now answerable | Collect 80+ more real RUAS enquiries; refresh out-of-scope cases; run the full suite and record it | **P0** |
| F10.4 | Sandbox preview | Implemented | C `console/preview` | ✅ | — | — | — |
| F10.5 | Self-serve signup, billing | — | — | — | L2 | — | — |
| F10.6 | Content readiness review: current year, conflicts, an owner for every document | Part of readiness | A `health.py` | 🟡 | Owners missing | As F4.5 | P1 |

## 8. Non-functional requirements (section 16, Level 1)

| Area | PRD L1 target | Measured / found | Status | Recommended improvement | Priority |
|---|---|---|---|---|---|
| Chat latency | First checked sentence ≤ 5 s p95; full answer ≤ 10 s p95 | Median 6.3 s, **p95 23.9 s** (72 RUAS questions, free Gemini key); English often 1–3 s, Hindi median 16 s | ⚠️ | Paid Gemini key or lab model; cache translations of curated answers; answer curated matches without the model | **P0** |
| Voice latency | ≤ 3.5 s p95 | Not measured | 🧪 | Measure from Retell / Vapi logs | P0 |
| Concurrency | 20–40 chats, 5 voice sessions (load test) | `GET /load` exists; no load test on the current stack | 🧪 | One load test (e.g. 30 simulated chats) | P1 |
| Availability | Best effort with API fallback | Single laptop, quick tunnel whose address changes on every restart, in-memory Retell call map | 🟡 | Run on the lab server with a named tunnel; store Retell calls in the database | P1 |
| Accuracy | ≥ 90% on tenant eval set; 0 critical failures | **50%** on the 72-case RUAS set; 6 critical failures (partly stale cases) | ⚠️ | F1.4, F1.6, F10.3 | **P0** |
| Security | TLS, MFA for admins, RLS, secrets out of code, ASVS L1, 10.3 prerequisites | scrypt, rate limits, MFA, forced RLS, secrets in an ignored file all present; server prerequisites (10.3) not in this repo; two API keys pasted in chat not yet rotated | 🟡 | Rotate the Sarvam and Vapi keys; ASVS L1 checklist pass | P0 (keys) / P1 |
| Privacy | DPDP-aligned; data in India; **no external model calls in L1** (L2: opt-in, PII redacted, DPA) | Gemini: opt-in and PII masking (L2 pattern). **Voice providers: on for every college, raw audio and transcripts, no masking, outside India** | ⚠️ | Per-college opt-in for every external service; masking of transcripts sent onward; DPA; tell visitors before a call | **P0** |
| AI safety | Grounded only; no decisions or guarantees; injection defence; sensitive handoff; PII masked in traces | Checks, injection rules, sensitive handoff and trace masking all present | ✅ | Red-team list in the test set | P2 |
| Accessibility | WCAG 2.2 AA | axe-core 0 violations on 7 pages (9 Nov); call panel and comment box added since | 🧪 | Re-run axe and the keyboard script on the new parts | P1 |
| Localisation | English + Hindi text; English voice | EN/HI/KN text and voice offered; Hindi answers 5/10 | ⚠️ | F1.6 | P0 |
| Observability | Traces for 100% of turns; GPU and latency dashboards; alerts | Traces for every turn; no alerts, metrics endpoint or dashboards | 🟡 | Error-rate and latency alerts (even an e-mail) | P1 |
| Maintainability | Typed code; ≥ 70% unit coverage; infrastructure as code | Typed; coverage never measured; `deploy/` has only a README | ❌ | `pytest --cov` in CI; compose files | P1 |

## 9. Cross-cutting findings

| # | Finding | Evidence | Priority |
|---|---|---|---|
| T1 | **CI does not test the database code.** CI has no Postgres, so the 20 `*_db` test files skip (engine, multilingual, RLS, self-learning). The web app's typecheck, lint and 30 test files are not run in CI either | `.github/workflows/ci.yml`; `needs_db` skip markers | **P0** |
| T2 | **Contracts cover 17 of 167 routes.** The contract check only confirms those 17 exist; the other 146 (voice, Vapi, self-learning, most of the console) have no agreed contract. Fuller drafts sit in `docs/proposed-contracts/` | `scripts/check_contracts.py`; route comparison | P1 |
| T3 | **The test set no longer matches the knowledge base.** Out-of-scope cases (intake, placements, hostels) became answerable when the website was crawled; the gate therefore fails on correct behaviour | `packs/ruas/eval/questions.csv` r55–r71 | P0 |
| T4 | **Source attribution:** correct answers often cite a web page rather than the approved curated answer, so staff corrections to the curated answer may not reach visitors | 11 "wrong source" cases | P1 |
| T5 | **Very large functions:** the engine's turn function is ~650 lines; `gate.py` 1,738 lines; `Widget.tsx` 1,162 lines | file sizes | P2 |
| T6 | **Requires clarification with the guide:** (a) are Retell / ElevenLabs / Soniox / Sarvam / Vapi acceptable in the pilot, and under which agreement; (b) should the widget's language picker override a clear English question; (c) is Kannada in scope for L1 (the PRD lists it as L2) | — | ❓ P0 |

## 10. Summary

| Status | Rows (L1 functional requirements; F5.7–F5.8 and F6.1–F6.6 are grouped L2 rows) |
|---|---|
| ✅ Fully implemented | 33 |
| 🟡 Partially implemented | 10 |
| 🧪 Implemented but not properly tested | 12 |
| ⚠️ Implemented but incorrectly / against the PRD | 5 (F1.4, F1.6, F2.1, F2.2, F2.4) |
| ❌ Not implemented | 1 (F10.3) |
| — Level 2 only | 15 rows |
| ❓ Requires clarification | 3 questions for the guide (T6) |

Non-functional (section 16): 1 met (AI safety), 4 against the PRD (chat latency, accuracy,
privacy, localisation), the rest partial or unmeasured.

Before this is shown to leadership, the P0 items are: privacy gating of the voice providers (F2.1,
F2.2, F2.6), the Hindi / Kannada answer path and latency (F1.4, F1.6, NFR latency), the RUAS data
gaps (F4.3, F10.2), a refreshed and complete test set with a recorded full run (F10.3, T3), and a CI
that runs the database tests (T1).
