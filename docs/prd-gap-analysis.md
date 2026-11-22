# PRD v2.0 gap analysis (Level 1)

Audit of this repository against **AdmitAI PRD v2.0 (26 Oct 2026)**, the only version in the
repository, Level 1 scope only. Level 2 items (F1.9–F1.10, F2.7–F2.8, F3.5, F3.7–F3.8,
F4.9–F4.11, F5.7–F5.8, F6.1–F6.6, F7.6, F9.5, F10.5) are out of scope. Requirement wording is
paraphrased; the PRD itself belongs in the private repository (see "Repository hygiene" below).

| Pass | Date | Branch | What changed |
|---|---|---|---|
| 1 | 29 Oct 2026 | `local/prototype` | First audit; Module C e-mail, overdue callbacks, stream recovery, peak season |
| 2 | 3 Nov 2026 | `c/level1-completion` | Re-audit after PR #2 (Module B) and PR #6 (Module A); Module C picks up what A and B handed over |
| 3 | 7 Nov 2026 | `c/level1-completion` | Re-check of open items; F9.2 intents and trending, F9.4 model usage, handoff banner, two Module B checking fixes |
| 4 | 7 Nov 2026 | `c/level1-completion` | Beyond the PRD's letter: failed sign-ins and two-factor codes slowed down (12.3); launch finishes without the Colleges page open (F10.3); dashboard no longer holds a transaction during a Module B call; one more try for an uncited first sentence (F1.4); shared fees said once |
| 5 | 8 Nov 2026 | `c/level1-completion` | Console opens on "Needs you today" (PRD 13); rate limits shared by every process (12.3); connection pools in A, B and C; approvers can draft a missing answer from the college's documents; evaluation suites run one at a time and wait while visitors are answered |
| 6 | 8 Nov 2026 | `c/level1-completion` | WhatsApp sources no longer glued to the answer (channel consistency 13/13); tokens per answer (F9.4); consent and age not asked twice in one chat; automated accessibility check and its fixes (13) |
| 7 | 9 Nov 2026 | `c/level1-completion` | Degraded mode in the widget: approved answers when the assistant is down (10.2); estimated cost per model from a configured token price (F9.4) |
| 8 | 9 Nov 2026 | `c/level1-completion` | Staff photo on the handoff banner (13); spoken end-of-chat rating; SMS replies through any HTTP provider (F3.6); browser push without content (F7.5). Tests written, not yet run |
| 9 | 9 Nov 2026 | `c/level1-completion` | Challenging decisions: messaging steps isolated (one failure no longer stops all messaging); catalogue fee and duration questions answered without the model; gate reuses an unchanged baseline; the citation retry kept within the two-call budget (7.5). Tests written, not yet run |
| 10 | 9 Nov 2026 | `c/level1-completion` | Roadmap item 1: no eligibility verdicts about the visitor, cited or not (F1.4, F5.3); prompt answer-v5; critical test question d26 |
| 11 | 9 Nov 2026 | `c/level1-completion` | Kannada added on request: widget text, detection, fixed replies, WhatsApp, routing, safety checks, spoken numbers (beyond F1.6; wording needs native review) |
| 12 | 9 Nov 2026 | `c/level1-completion` | Product review (`docs/product-review.md`): SMS offered only with a provider, honest e-mail wording without a mail server, push switch says when it is not set up, spoken-rating hint only after voice |

**The closing assessment** is in [final-evaluation.md](final-evaluation.md). **What to do next** is in [third-order-improvements.md](third-order-improvements.md): the remaining
improvements, ranked by impact, feasibility and risk.

**Method.** Each row was checked against the code, not against a module's own README: routes,
migrations, UI and jobs were read, and the behaviour was traced to where it is enforced. A
claim counts as ✅ only with a passing test, or a live run, behind it.

**Status key.** ✅ Implemented · 🟡 Partial · ⬜ Missing · ❌ Non-compliant · ❓ Needs verification.
**Priority** is for the remaining gap: Critical, High, Medium, Low. **Owner** is per PRD Appendix C.

## 1. Summary

**Before this pass.** Modules A and B had each declared Level 1 complete (PRs #6 and #2), and
their tests confirm it: A 99 passed with 6 skipped for missing system packages, B 243 passed.
Each left a list of asks for Module C, and **Module C had picked up none of them**:

- guardian codes by WhatsApp still answered 503, although B's README said C already used B's
  endpoint (❌ as documented);
- no WhatsApp delivery for staff replies, acknowledgements or overdue updates;
- implicit signals never forwarded to A;
- the weekly digest never e-mailed;
- second review and delegates not reachable from the console;
- quality and unanswered-question data from A not shown;
- no Markdown in the widget.

Module C's `requirements.txt` also lacked two packages it imports (`pyyaml`,
`python-multipart`), so its tests could not run in a clean environment.

**After this pass.** All of the above is built in `experience/`, with 11 new API tests and 7 new
web tests, and was checked live against the real Modules A and B (section 5). What remains is
mostly lab infrastructure, a Meta account, or a decision for the guide.

## 2. Traceability matrix (Level 1)

| Req | Owner | Status | Evidence | Remaining gap | Priority |
|---|---|---|---|---|---|
| F1.1 widget, `/chat` full page | C | 🟡 | `web/src/app/widget`, `public/embed.js` | A Next.js page in an iframe, not the small Preact web component; 4G load time not measured | Medium |
| F1.2 checked streaming, Markdown, citations | B, C | ✅ | B `checks.py`; C `Widget.tsx` `markdownInline` + `AnswerText.test.tsx` | Inline Markdown only (bold, italics, code, headings, bullets); tables are not rendered | Low |
| F1.3 context, summary | B | ✅ | B `context.py`, `test_summary_and_hours.py` (PR #2) | — | — |
| F1.4, F1.4a, F1.4b grounding, sentence checks, async review | B | ✅ | `checks.py`, `review.py`, `test_fact_binding.py` | — | — |
| F1.5 starter chips, quick replies | B, C | ✅ | `Widget.tsx` | — | — |
| F1.6 English + Hindi | B, C | 🟡 | `i18n.ts`; `NATIVE_ANSWER_LANGUAGES` | Hindi answers off on the 3B model (configuration) | Low (lab) |
| F1.7 talk to a human, F1.8 rating | C | ✅ | `Widget.tsx`, `routes/feedback.py` | — | — |
| F1.11 WhatsApp channel, window, opt-in, templates | B | 🟡 | B `whatsapp.py` notify: text in window, template outside, opt-out | Real Meta verification and approved templates; tested with signed fake webhooks only | Medium (Meta account) |
| F1.12 WhatsApp formatting | B | ✅ | `split_messages`, buttons | — | — |
| F1 errors: model fallback, dropped stream, rate limits | B, C | ✅ | `policy.py`; `GET /chat/{id}/last`; 429 + Retry-After | — | — |
| F2.1 LiveKit/WebRTC, hands-free | B, C | 🟡 | B issues LiveKit room tokens when configured (`livekit.py`) | No LiveKit worker; push-to-talk over HTTP | Medium (lab) |
| F2.2 streaming captions | B | 🟡 | caption states | No live partial transcripts (needs the LiveKit worker) | Medium (lab) |
| F2.3, F2.4 voice style, TTS, barge-in | B, C | ✅ | `voice.py`, `toggleVoice` | Barge-in time not measured | Low |
| F2.5 voice latency | B | ❓ | about 5.9 s on laptop CPU | Measure on the lab GPU | — |
| F2.6 transcript, consented audio | B | 🟡 | transcripts kept; audio never stored | No audio-recording consent path (privacy-safe deviation) | Low |
| F2.9 spoken lead capture | B | ✅ | `spoken_lead.py` | — | — |
| F2.10 voice test-set switch | B, A | ✅ | A `voice_eval.py`; B `POST /voice/eval-result` (PRs #2, #6) | Meaningful only with the lab voice model | — |
| F3.1, F3.1a, F3.3 routing, multi-intent, handoff package, office hours | B | ✅ | `router.py`, `hours.py` | — | — |
| F3.2 queues | C | ✅ | RLS policies, console | — | — |
| F3.4 callback acknowledgement with expected time | C | ✅ | e-mail; **WhatsApp through B** when the person chose it (`routes/handoffs.py`, `messaging.deliver_whatsapp`) | SMS not built; WhatsApp outside 24 h needs an approved template | Low |
| F3 errors: overdue callback | C | ✅ | `messaging.escalate_overdue`: reminder, escalation, apology by e-mail **and WhatsApp** | — | — |
| F3.6 staff reply by e-mail / WhatsApp, offered as correction | C | 🟡 | `send_email`, **`send_whatsapp`** (refusals shown on the task) | SMS not built | Low |
| F4.1 upload formats, OCR | A | ❓ | A `ocr.py` (PR #6) | OCR tests skip here: Tesseract is not installed on this Mac (system package) | Medium (server image) |
| F4.2–F4.6, F4.8 crawl, catalogue, curated Q&A, metadata, versions | A | ✅ | A tests | — | — |
| F4.7 chunk, embed, pgvector HNSW | A | ❓ | A `search.py` pgvector path, falls back to `real[]` | pgvector tests skip here: extension not installed | Low (lab) |
| F5.1, F5.4, F5.6 capture, scoring, UTM | B, C | ✅ | `leads.py`, `scoring.py` | — | — |
| F5.2 consent | C | ✅ | `app.consents` | — | — |
| F5.3 minors: guardian code by e-mail or WhatsApp | C, B | ✅ | e-mail sent at once; **WhatsApp code held by B and sent only when the guardian writes in**; C keeps only the hash; 503 if it cannot be sent | Real WhatsApp delivery needs the Meta account | Low |
| F5.5 lead list, pipeline, CSV | C | ✅ | `export_leads` | — | — |
| F6.7 consent and UTM foundations | C | ✅ | | — | — |
| F7.1–F7.4 inbox, conversation, corrections, review queue | C | ✅ | console pages | — | — |
| F7.5 notifications: in-app, e-mail, browser push | C | 🟡 | in-app + e-mail | Browser push not built | Low |
| F7.7 approval inbox, batch, weekly digest | C, A | ✅ | `approvals/page.tsx`; **digest e-mailed weekly** (`messaging.weekly_digest`) | — | — |
| F8.1, F8.2, F8.4 feedback, rating, corrections | C | ✅ | | Spoken "was this helpful?" not built | Low |
| F8.3 implicit signals | C, A | ✅ | **`signal.logged`** for rephrase, talk to a human, voice hang-up, barge-in (`routes/signals.py`); A consumes it | — | — |
| F8.5, 8.2 weekly report, triage | A | ✅ | `health.py`, `learning.py` | — | — |
| 8.3 impact, SLA, delegate, second review, peak season, digest | A, C | ✅ | A escalation + sample (PR #6); C: **escalation badge, delegates editor, second-review list and verdict, digest** | — | — |
| 8.4 gate, rollback, coordinated-feedback limit | A, C | ✅ | `gate.py`, `limit_bursts` | — | — |
| F9.1 overview metrics (3.5 definitions) | C | ✅ | `routes/analytics.py`; lead capture uses qualifying conversations | — | — |
| F9.2 topics: intents, trending, unanswered | C, B | ✅ | B `GET /analytics/topics` (top intents; questions repeated across 2+ conversations, masked; each against the previous week); C `/analytics/topics`, `TopicsView` | — | — |
| F9.3 quality: acceptance, eval score per release, proposals | C, A | ✅ | **`GET /analytics/quality`** from A `/reports/quality`; page says when A is down | — | — |
| F9.4 platform ops: latency, GPU, model usage, cost | C, B | 🟡 | p95 first-sentence latency per channel; **model usage** from B `/analytics/turns` `by_model`, with **tokens per answer** reported by the gateway (B migration 008), shown in `ByModel` | GPU utilisation and API cost per tenant: lab metrics | Medium (lab) |
| F10.1–F10.4, F10.6 Tenant Pack, readiness, launch, preview | A, C | ✅ | `tenant_pack.py`, `routes/platform.py`, A `catalogue_cases.py` | — | — |
| 5.4 MFA, token lifetimes, break-glass | C | ✅ | `routes/auth.py` | — | — |
| 7.5, 9.5 latency budget, model gateway | B | 🟡 | ≤ 2 hot-path calls; policy enforced; LiteLLM config | Explicit pipeline, not LangGraph; DeepSeek comparison needs the GPUs | Low (lab) |
| 9.4 RAG pipeline, 17 content operations | A | ✅ | hybrid + RRF + reranker | — | — |
| 11.3 RLS, 11.4 retention and erasure | All | ✅ | per-module RLS tests; walking skeleton erasure checks | — | — |
| 12.2 webhooks | C | ✅ | `conversation.closed` not sent | B does not close conversations | Low |
| 12.3 API standards | All | 🟡 | problem+json, 429; failed sign-ins and two-factor codes limited with `Retry-After` (pass 4) | `/v1` prefix is the guide's contract decision | Low |
| 13 UI/UX, WCAG 2.2 AA | C | 🟡 | labels, live regions, 44 px targets, collapse after 12 lines; **handoff banner** with the staff member's first name (`HandoffBanner`, `GET /handoffs/mine`); **automated WCAG 2.2 A/AA check (axe-core) of 7 pages, light and dark: 0 violations** after fixing contrast (badges, secondary buttons in dark mode, launcher colour, notice dates) and a tab list holding a checkbox | No staff photo, because profiles hold none; no audit with a screen reader in this pass | Low |
| 16 observability | B, guide | 🟡 | `chat.traces` | No alerts, Langfuse or Prometheus (lab) | Low (lab) |
| 10, 18 deployment, degraded mode, pilot ops | Guide | ⬜ | local scripts; degraded export in A | Compose, edge page, backups | — (guide) |
| 20 evaluation | A, B | 🟡 | suites, gate, judge (PR #6) | Real held-out questions per college; judge on the lab GPUs | — (lab) |

## 3. Plan for this pass (all in `experience/`)

Ordered by priority. Every API change is additive; nothing in `contracts/` was edited.

| # | Change | Requirements | Depends on |
|---|---|---|---|
| 0 | Declare `pyyaml` and `python-multipart` in Module C's requirements | CI, conventions | — |
| 1 | Guardian code by WhatsApp through B's guardian-initiated endpoint; honest 503 if B refuses or is down | F5.3 | B `POST /channels/whatsapp/guardian-code` (exists) |
| 2 | WhatsApp in `app.message_outbox` delivered by B's notify: acknowledgements, overdue updates, staff replies; refusals final and shown on the task | F3.4, F3.6, F3 errors | B `POST /channels/whatsapp/notify` (exists) |
| 3 | `signal.logged` outbox events to A | F8.3 | A `POST /events` (accepts it) |
| 4 | Weekly digest e-mail and the monthly second-review sample | 8.3, F7.7 | A `/reports/digest`, `/improvements/second-reviews/sample` |
| 5 | Approval inbox: escalation, second review, delegates | 8.3 | A `/improvements?second_review=`, `/learning/settings` |
| 6 | Dashboards: quality over time, unanswered questions | F9.2, F9.3 | A `/reports/quality`, `/reports/weekly` |
| 7 | Inline Markdown in the widget, without HTML | F1.2 | — |

Deferred, with reasons: the Preact widget rewrite (F1.1, a large separate decision); intents and
trending (F9.2, needs B); GPU, usage and cost (F9.4, lab and B); SMS (no provider in Level 1);
browser push (F7.5, low value for Level 1).

## 4. Changes made

| Change | Files |
|---|---|
| Migration: `signal.logged` event type; `messaged` handoff event | `experience/db/migrations/009_whatsapp_signals_digest.sql` (new) |
| Module B client that tells a refusal (final) from an outage (retry) | `experience_api/clients.py` (`assistant_post`, `AssistantRefused`) |
| Guardian code by WhatsApp; optional `language` | `routes/consents_leads.py`, `schemas.py` (`GuardianStartIn.language`) |
| WhatsApp delivery, overdue WhatsApp update, weekly digest, `approvers` audience | `experience_api/messaging.py` |
| WhatsApp acknowledgement; `send_whatsapp` on replies | `routes/handoffs.py`, `schemas.py` (`ReplyIn.send_whatsapp`) |
| Implicit signals to A | `routes/signals.py` |
| Second review, delegates, learning settings | `routes/approvals.py`, `schemas.py` |
| Quality and topics endpoints | `routes/analytics.py` |
| Console: WhatsApp reply option, escalation badge, second reviews, delegates, quality and unanswered views | `web/src/app/console/{handoffs/[id],approvals,analytics}/page.tsx`, `web/src/components/console/{proposals,quality}.tsx`, `web/src/lib/types.ts` |
| Widget: inline Markdown; Hindi hint for WhatsApp guardian codes; language sent | `web/src/components/widget/{Widget,ContactFlow}.tsx`, `web/src/lib/i18n.ts` |
| Missing dependencies | `experience/requirements.txt` |
| Tests | `experience/tests/test_exp_api_db.py` (+11), `web/src/**/*.test.ts(x)` (+7) |
| Docs and proposed contracts | `experience/README.md`, `docs/proposed-contracts/experience.openapi.json` (regenerated), `docs/proposed-contracts/events.schema.json` (`signal.logged`) |

## 5. Test results and evidence (3 Nov 2026, this Mac)

| Run | Before | After |
|---|---|---|
| `pytest` experience | could not run (missing `yaml`, `multipart`); 86 passed once declared | **97 passed** (+11) |
| `pytest` knowledge (not changed) | 99 passed, 6 skipped (Tesseract, font, pgvector not installed) | same |
| `pytest` assistant (not changed) | 243 passed | same |
| web `vitest` | 37 passed | **44 passed** (+7) |
| `ruff check`, `ruff format --check`, `tsc`, `eslint`, `prettier --check` | clean | clean |
| `next build` | — | succeeds |
| Contract check, Module C (every endpoint in `contracts/experience.openapi.yaml` implemented) | pass | pass; 6 paths added, none removed |
| `scripts/e2e_local.py` live, on an isolated database | — | **19/19 passed** |
| Live smoke of the new C → A and C → B calls (scratch script, not committed) | — | see below |

The live runs used a separate database (`admitai_c_verify`) so the existing `admitai_local` data
was not reset. The three modules ran on 127.0.0.1 with `qwen2.5:3b` through Ollama.

**Live smoke, 19/19 passed** (second run; the first run's failures were bugs in the scratch
script: one number used for two people, and matching B's outbox by a field it does not keep):

- F5.3: C hands the code to B, B holds one sealed code, the code reaches the guardian only after
  the guardian writes in, and it verifies in C.
- F3.4, F3.6: outside the 24-hour window with no approved template, B refuses, C records it and
  the task shows it. After the person writes in, the staff reply goes out as WhatsApp text, and
  C's outbox forgets the number and the text.
- F8.3: a rephrase logged in C reaches A as `signal.logged`.
- 8.3, F7.7, F9.2, F9.3: the digest run reaches A, records the week and does not repeat; second
  reviews, settings, quality, topics and delegates all work against the real A.

One unexplained event: in the first run, one staff request got 401 "Sign in again" about two
minutes after the same token had worked. It did not recur in the second run or in a separate
130-second token test (tokens last 15 minutes as specified). It is recorded here rather than
explained away.

Not verified: delivery through a real SMTP provider or the real WhatsApp Cloud API (B writes to its
local outbox without a Meta token); the console screens in a real browser (type-checked, built and
unit-tested, not clicked through); OCR and pgvector on this machine.

## 6. Still partial or missing, and who it depends on

1. **F9.4 GPU per tenant** (Medium): lab metrics. Cost per tenant is now estimated from tokens per answer and a configured price (`TOKEN_PRICES_INR`); the price itself is the platform's decision.
2. ~~Staff photo on the handoff banner~~: built in pass 8 (staff add their own).
3. **F1.1 Preact web component under 60 kB** (Medium): a rewrite, so a team decision.
4. **Meta business verification and approved templates** (F1.11): until then, WhatsApp outside
   the 24-hour window is refused, and the task says so.
5. **Lab-only** (guide and infrastructure): LiveKit worker, Tesseract and pgvector in the images,
   DeepSeek judge, GPU latency, Langfuse, Compose, degraded-mode edge page, `/v1`.
6. **Low:** SMS needs a provider contract (the code is built, pass 8); browser push needs a VAPID key
   per deployment (built, pass 8); spoken end-of-chat rating built (pass 8). Tables in widget Markdown are left out on
   purpose: Module B's sentence checks need a source number per sentence, so a table would be
   blocked before it reached the widget.

## 7. Deviations from the PRD and why

- WhatsApp guardian codes are **guardian-initiated** (the guardian writes to the college first,
  then gets the code), so the college never messages a number nobody has confirmed. The visitor is
  told which number to write to.
- A WhatsApp message B refuses (opt-out, no template outside the window) is **not retried** and
  is not silently switched to another channel. The task records it, and staff phone or e-mail.
- The digest goes to College Admins and admission officers. Faculty see their departments' items
  in the inbox but are not e-mailed the college-wide digest.
- Unanswered questions come from A's weekly knowledge-gap list (masked), not from a separate topic
  model.
- Markdown is inline only, so the 12-line collapse keeps working and no HTML is ever rendered.

## 8. Contract changes for the guide (all additive)

Module C (`docs/proposed-contracts/experience.openapi.json`):

- new paths: `GET /analytics/quality`, `GET /analytics/topics`, `GET /approvals/settings`,
  `PUT /approvals/delegates`, `GET /approvals/second-reviews`, `POST /approvals/{id}/second-review`;
- new fields: `ReplyIn.send_whatsapp`, `GuardianStartIn.language`.

Events (`docs/proposed-contracts/events.schema.json`): `signal.logged` (C → A), as Module A asked.

## 9. Repository hygiene (for the guide; not changed in this pass)

- `AdmitAI-PRD-v2.0.pdf`, `AdmitAI-Executive-Brief.pdf` and `AdmitAI-Module-Choices.pdf` are
  committed to this **public** repository. The root `CLAUDE.md` says the specification stays in the
  private repository. Removing them needs the guide, and possibly a history rewrite.
- `packs/ruas/` is committed, although `HANDOVER.md` says the RUAS content must not go public.
- `local.env.sh` creates `local.secrets.sh` (service keys, demo password) and `.local/` (the
  visitor-token signing key), but **neither is in `.gitignore`**, although `LOCAL.md` calls them
  git-ignored. One `git add .` would publish them. Add both to `.gitignore` (a guide-owned file).
- `assistant/README.md` said Module C already used B's guardian-code endpoint. That was not true
  before this pass; it is now.
- `docs/` is shared; this file stays here because pass 1 put it here.
