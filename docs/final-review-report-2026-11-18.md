# AdmitAI — final review report (18 Nov 2026)

An evidence-based assessment of the AdmitAI application as it exists on `main` at `908511b`, run
on the local Mac with the RUAS college (Ramaiah University of Applied Sciences), against PRD v2.0
and the quality expected of a professional university assistant.

This report summarises twelve review phases. The detail, with every measurement, is in the
companion reports in `docs/`:

| Report | Covers |
|---|---|
| `prd-traceability-2026-11-18.md` | PRD compliance matrix (F1–F10, section 16) |
| `frontend-ux-review-2026-11-18.md` | public site, widget, console screens |
| `staff-console-review-2026-11-18.md` | staff workflows, live role tests |
| `backend-review-2026-11-18.md` | API, database, scalability, logging |
| `ai-rag-review-2026-11-18.md` | ingestion, retrieval, models, prompts, grounding |
| `voice-review-2026-11-18.md` | Retell, push-to-talk, telephone, languages |
| `security-privacy-audit-2026-11-18.md` | security and privacy |
| `testing-qa-review-2026-11-18.md` | test runs and coverage |
| `third-order-review-2026-11-18.md` | consequences and design challenges |
| `improvement-roadmap-2026-11-18.md` | 74 prioritised items with all fields |

**How to read the labels:**
**[V] Verified** — seen in code at the cited place or measured on the running system on 18 Nov.
**[A] Assumption / inference** — reasoned from evidence but not directly observed.
**[R] Recommendation** — what to do; not a finding.

---

## 1. Executive summary

AdmitAI is a well-engineered **Level 1 prototype** with a sound architecture and unusually careful
answer safety. It is **not ready for real applicants**. The gap is not the plumbing — it is answer
quality, privacy governance, data completeness and verification.

**What is genuinely strong [V]:**
- Every answer sentence is checked by code before release: numbers against the catalogue,
  citations required, promises blocked (`assistant/assistant_api/checks.py`). Live injection probes
  were all refused or blocked.
- Tenant isolation is enforced by PostgreSQL row-level security on all 60 tenant tables, with
  non-superuser roles for request handling; no SQL injection; visitor sessions isolated (live test).
- A real university's website is crawled into a knowledge base of 102 documents, 81 programmes and
  214 curated answers; follow-up questions resolve correctly ("what about its fees?").
- 866 automated tests pass; type check and lint are clean.

**What fails the bar [V]:**

| Measure | PRD target | Measured |
|---|---|---|
| Answer accuracy on the college's test set | ≥ 90%, 0 critical failures | **50%** (36/72), 6 critical failures |
| First checked sentence, p95 | ≤ 5 s | **23.9 s** (Hindi median 16 s) |
| Voice, end of speech to first audio | ≤ 3.5 s p95 | Retell per-call medians **2.8–5.0 s** |
| Real test questions per college | ≥ 150 | **73** |
| Launch readiness checklist (the system's own) | all blocking checks pass | **5 of 7 blocking checks fail** |
| External model calls in Level 1 | none (L2: opt-in, redaction, DPA) | voice providers on for every college, raw audio, no opt-in |

**The single biggest risk** is privacy: student voices and transcripts — including spoken phone
numbers — go to US-hosted voice providers without the college opting in, masking misses one-time
codes and names, and retention rules never run. A university legal review would stop the pilot
here regardless of answer quality.

**The second** is credibility: the presentation describes training a 13–15B model with FAISS and
LangGraph; none of that exists. The system is retrieval over the college's own data with a hosted
Gemini model.

**Verdict:** pilot-ready after the 10 P0 items and the accuracy work (P1-01 to P1-07) are done;
production-ready only after Level 2 items and an operated deployment.

## 2. Overall assessment

| Dimension | Rating | Basis |
|---|---|---|
| Architecture | **Good** | three modules, own schemas, typed APIs, service keys, outbox in C [V] |
| Answer safety (grounding) | **Good** | deterministic sentence checks; injection probes refused [V] |
| Answer quality | **Poor** | 50% test pass; Hindi 5/10, Kannada 9/16 real answers [V] |
| Retrieval | **Weak** | Hit@1 48%, Hit@4 70%; duplicates and fragments [V] |
| Latency | **Poor** | p95 23.9 s; 3–4 model calls for Indian languages [V] |
| Public UI | **Good, with demo blockers** | professional look; 0 axe violations; dev badge, 25-s "…" waits [V] |
| Staff console | **Partial** | correct permissions; knowledge read-only; inbox noisy; no password reset [V] |
| Voice | **Partial** | push-to-talk works in 3 languages; half of Retell calls fail; phone never completed [V] |
| Security | **Good foundations, critical privacy gaps** | RLS, auth, webhooks solid; C1–C4 open [V] |
| Testing | **Broad but unverified where it matters** | 866 pass; DB tests never run in CI; 62 engine tests run nowhere [V] |
| Operations | **Not production** | one laptop, quick tunnel, no alerts, no backups in repo; rebooted twice during review [V] |
| Documentation vs reality | **Inconsistent** | slides and parts of README/PRD disagree with the code [V] |

[A] Overall, the project has most Level 1 *mechanisms* built, but roughly half of what a pilot needs
to *trust* them (accuracy evidence, data completeness, governance, verified CI, stable hosting) is
still missing. This is a judgement, not a measured percentage.

## 3. PRD compliance matrix (summary)

Full matrix: `prd-traceability-2026-11-18.md` (every F-requirement with files, gap, fix, priority).

| Status | Rows |
|---|---|
| ✅ Fully implemented, seen working | 33 |
| 🟡 Partially implemented | 10 |
| 🧪 Implemented but not properly tested | 12 |
| ⚠️ Implemented incorrectly / against the PRD | 5 — F1.4 grounding outcome, F1.6 languages, F2.1/F2.2/F2.4 external voice |
| ❌ Not implemented | 1 — F10.3 launch test set (73 of 150) |
| Level 2 only | 15 |

Non-functional: AI safety met; **chat latency, accuracy, privacy and localisation against the PRD**;
the rest partial or unmeasured [V]. Contracts cover **17 of 167 routes** (`contracts/*.yaml` vs
code) [V].

Requires clarification with the guide [R]: (a) are Retell / ElevenLabs / Soniox / Sarvam / Vapi
acceptable in the pilot and under which agreement; (b) should the language picker override a clear
English question (changed on 17 Nov to "reply in the typed language"); (c) is Kannada in L1 scope
(PRD says L2).

## 4. Frontend review

Detail: `frontend-ux-review-2026-11-18.md`.

**Verified [V]:**
- The public site reads as a university admissions page (RUAS palette, programme catalogue with
  fees, notices), not a generic AI page. axe-core: 0 WCAG 2.2 A/AA violations on 14 pages; no
  horizontal scroll at 390 px.
- A development badge ("N 1 Insight") overlays headings, the widget and the console's sign-out on
  every page, because the demo runs `next dev`.
- Asked "Is there a hostel?", the widget showed only "…" for **24.6 s** (Gemini timed out at 15 s,
  then `qwen2.5:3b` answered: "Yes, there is a hostel. [1] and [2] and [3] all mention that…").
- Widget: title truncated; empty gap before suggestions; "Withdraw consent" shown before consent;
  not full-screen on phones, launcher overlaps.
- Knowledge page renders **29,771 px**; approval inbox **7,400 px**; console navigation cut off at
  390 px; Next.js default 404; favicon 404.

**[R]** Keep the public design; fix behaviour only: production build for demos, status text during
waits, full-screen widget on phones, consent link after consent. Workflow changes go to the console.

## 5. Staff console review

Detail: `staff-console-review-2026-11-18.md`.

**Verified [V]:**
- All **57** API paths the console calls exist in Module C; no dead buttons.
- Live permission run (8 roles × 20 endpoints): server-side checks match PRD 5.3; department roles
  get only their department; "Publish now" admin-only on screen (`approvals/page.tsx:175,272`) and
  server (`routes/approvals.py:255`).
- **Super-admin reads raw feedback with comments** (`GET /feedback` → 200) — PRD 5.3 says
  aggregates only.
- **Knowledge page is read-only** (`app/console/knowledge/page.tsx` makes only GET calls) although
  Module A has upload, sources, fee edit, document and curated-answer APIs.
- Approval inbox lists injection attempts, an OTP request and another student's marks as
  "missing knowledge"; rejecting needs the batch bar.
- No password reset of any kind (`schemas.UserPatch` has only `reset_mfa`); temporary passwords
  never have to be changed; role changes save on dropdown change (`admin/page.tsx:102`).
- Analytics shows "My OTP is 482913" under "personal details are masked"; counts include test
  traffic and console previews.
- Audit trail records 33 action types; failed sign-ins are not audited.

## 6. Backend review

Detail: `backend-review-2026-11-18.md`.

**Verified [V]:**
- Uniform `application/problem+json` errors with trace ids across modules (identical
  `problems.py` in A, B, C); Pydantic-typed bodies except `PUT /tenant-settings`
  (`assistant/routes/chat.py:308`, `config: dict`).
- Connection pooling with per-transaction tenant setting (`db.py`); no module touches another
  module's schema.
- Staff endpoints answer in 5–90 ms; search 1.8 s cold, ~50 ms memoised.
- Process-memory state: Retell and Vapi call registries (`retell.py:39`, `vapi.py:57`), Module B
  rate limits (`guard.py:105`).
- B→C calls have no retry: a spoken number is dropped (`voice_turns.py:200`), a WhatsApp opt-in is
  recorded as unsaved while the chat continues (`whatsapp.py:824`).
- Module A swallows errors in start-up recovery and sweeps (`knowledge_api/main.py:40–52`).
- PII masking implemented twice with different patterns (`knowledge_api/pii.py`,
  `assistant_api/guard.py`).
- Lists unpaginated for curated answers (136 KB) and documents (56 KB); no `/v1` prefix.

**[R]** No technology replacement is justified; fix within FastAPI / psycopg / PostgreSQL.

## 7. Database review

**Verified [V]:**

| Schema | Tables | FKs | Indexes | RLS on / forced |
|---|---|---|---|---|
| app (C) | 25 | 40 | 39 | 24 / 23 |
| chat (B) | 13 | 3 | 20 | 12 / 12 |
| kb (A) | 10 | 6 | 16 | 8 / 8 |
| learn (A) | 16 | 4 | 25 | 16 / 16 |

- Request roles `admitai_app`, `admitai_chat`, `admitai_kb`: not superuser, no `BYPASSRLS`; tables
  owned by `postgres`.
- **Runtime superuser use:** Module A gate and learning (`gate.py:576–1717`, `learning.py:745`) and
  Module C workers (`webhooks.py:203`, `webpush.py:117`) connect as `postgres`.
- `chat.voice_audio` and `chat.wa_messages` have no FK to conversations; 32 tenant tables lack a
  tenant-first index (fine at current size).
- pgvector is **not installed** locally: vectors are searched in memory (`search.py` fallback);
  HNSW path untested here.
- Retention sweeps exist but are never scheduled.

**[A]** The schema supports the intended workflows (answer trail, catalogue, curated history,
proposals, consents, leads, handoffs, audit); no missing core entity was found.

## 8. AI / RAG review

Detail: `ai-rag-review-2026-11-18.md`.

**What runs vs what is documented [V]:**

| | PRD | Slides 9, 10, 14 | Running |
|---|---|---|---|
| Answers | Qwen2.5-14B local | self-designed/trained 13–15B Transformer | **Gemini 3.1 Flash-Lite** (opt-in), fallback `qwen2.5:3b` |
| Router / fallback | Llama-3.1-8B | — | `qwen2.5:3b` (`MODEL_ALIASES` maps both PRD names to it) |
| Judge | DeepSeek-R1 distills | — | configured, **not installed** |
| Vector store | pgvector HNSW | FAISS / Chroma | pgvector schema; in-memory locally |
| Orchestration | explicit pipeline | LangGraph agents | explicit pipeline (`engine.py`) |
| Training | none in L1 | GPU fine-tuning | **none** |

**Measured [V]:**
- Retrieval (54 in-scope cases): Hit@1 **48%**, Hit@4 **70%**, Hit@8 **74%**; Kannada raw Hit@1 31%.
- The website FAQ page duplicates all 214 curated answers; the copy is top-1 in 30 of 54 searches;
  13 answers cite the copy.
- Chunks: median 343 characters (PRD ~400 tokens); **32% under 200 characters**; fragments like
  "60 Seats" filed under the wrong heading.
- **0 of 214** curated answers have question variants.
- Model calls per turn: English median 1; **Hindi/Kannada median 3, max 4** (PRD ≤ 2); each ~3.5 s.
- Gemini answer: ~5,500 input tokens, ~390 output.
- Five injection probes refused or blocked; three-turn follow-up correct; one Hindi/Kannada
  contradiction (entrance exam needed or not).
- Prompt lacks "sources are data, not instructions" (`prompts.SAFETY_RULES`).

## 9. Voice assistant review

Detail: `voice-review-2026-11-18.md`.

| Channel | Status | Evidence [V] |
|---|---|---|
| Website push-to-talk (EN, HI, KN) | ✅ working | 6/6 synthetic round trips heard and spoken back; 71 real turns |
| Website hands-free (Retell) | 🟡 English only seen | 74 calls: 37 normal, 16 WebSocket runtime errors, 12 could not connect, 9 not joined |
| Telephone (Vapi) | ⚠️ not working | one call ever, `assistant-request-returned-error` |
| WhatsApp voice notes | 🧪 built, never run | WhatsApp not connected |
| LiveKit | 🧪 built, never run | `LIVEKIT_URL` unset |
| Bolna.ai | ⛔ not adopted | `assistant/README.md:804` |

Not claimable today [V]: phone calls, Hindi/Kannada hands-free calls, WhatsApp voice,
real-speaker accuracy, the 3.5-s voice target.

## 10. Security review

Detail: `security-privacy-audit-2026-11-18.md`. No secrets or personal data appear in any report.

**Critical [V]:** (C1) voice providers get raw voice and transcripts without opt-in; (C2) masking
misses OTPs and names, which reach staff screens and Gemini; (C3) super-admin reads raw feedback;
(C4) two provider keys were pasted in a chat session and not rotated.

**High [V]:** runtime superuser connections; no password reset / forced change; retention never
runs; crawler follows redirects to any host (`crawl.py:35`, `site_kb.py:211`); widget usable from
any site (`frame-ancestors *`, no origin check on `/widget/session`).

**Verified strengths:** no SQL injection (all formatted SQL uses fixed identifiers); live session
isolation; constant-time webhook and key checks; CORS allow-list; zip upload safe; none of 25
secret values in git history or logs; production start-up guard in all three services.

## 11. Testing and quality review

Detail: `testing-qa-review-2026-11-18.md`.

| Suite | Result (run 18 Nov) [V] |
|---|---|
| Module C | 124 passed |
| Module A | 183 passed, 6 skipped (OCR: no Tesseract; pgvector absent) |
| Module B | 452 passed |
| `assistant/tests/test_engine_db.py` (62) | **not run** — exhausted memory; second attempt rebooted the Mac |
| Web | 107 passed; `tsc` 0 errors; eslint clean |
| **Total** | **866 passed, 0 failed, 6 skipped, 62 not run** |

- CI (`.github/workflows/ci.yml`) has no PostgreSQL, so **all 20 database test files skip**, and no
  web checks run.
- Tests that pass but do not match reality: the Vapi inbound test passes while the real call failed;
  masking tests have no OTP/name cases.
- No console page tests, no browser tests, no load test, coverage never measured.
- End-to-end scripts (`scripts/e2e_*.py`) **not run**: they write into the real local database.

## 12. Critical gaps

1. **Privacy governance** — external voice processing without opt-in; masking gaps; retention not
   running; super-admin access (C1–C3, H3).
2. **Answer quality** — 50% on the college's own test set; Indian languages weakest.
3. **Knowledge ownership** — staff cannot edit the catalogue; the website overwrites it unseen.
4. **Verification** — the engine's tests run nowhere; CI proves only unit tests.
5. **Reliability of voice** — half of calls fail; phone never completed.
6. **Honest documentation** — slides and README describe a different system.

## 13. Third-order observations

Detail: `third-order-review-2026-11-18.md`. [A] marks consequences that are reasoned, not observed.

- [V→A] Website changes are published without review; [A] over time trust in the assistant will
  track the quality of a website the admissions office does not control.
- [V→A] Duplicates and fragments grow faster than useful text; [A] retrieval worsens as content
  grows, and staff corrections stop reaching visitors, ending the learning loop.
- [V→A] Indian-language turns cost 3× the calls; [A] Kannada speakers get a slower, poorer service
  at a Karnataka university.
- [V→A] Five voice paths and three "model stories"; [A] upkeep displaces quality work after hand-over.
- [V→A] Self-learning needs three agreeing sessions; RUAS has 2 feedback rows in 30 days; [A] it
  will rarely act during a pilot.
- [A] Adoption will be decided by the admissions office (knowledge ownership) and legal office
  (data flows), not by model quality.

Design decisions challenged [R]: crawl should propose, not publish; index curated answers once;
translate approved content once; 4 sources instead of 8; one voice path plus phone; paid model under
an agreement; self-learning off until volume; roles only with workflows; present what exists.
Not recommended [R]: fine-tuning, a new vector database, LangGraph, more voice providers, Redis.

## 14. Prioritised improvement roadmap

Full roadmap with problem, evidence, why, solution, benefit, complexity, dependencies and tests:
`improvement-roadmap-2026-11-18.md` (**P0 10 · P1 22 · P2 30 · P3 12**).

**P0 (must fix first):** rotate keys · per-college opt-in for voice providers · shared masking with
OTP/name cases · super-admin aggregates · schedule retention · outbox in B (no lost leads/consents)
· worker role instead of superuser · CI with database and web checks + split engine tests · fix the
phone path · stable address and DB call registries for Retell.

**P1 highlights:** remove FAQ duplication, merge fragments, add variants; pre-translate approved
content; latency (6-s first-token wait, 4 sources, paid key); decide the model and correct the
slides; complete RUAS data; editable knowledge in the console; crawl proposes changes; clean
approval inbox; password reset; refreshed test set ≥ 150 with `scripts/eval_ruas.sh`; WhatsApp;
widget origins; crawler address checks; contracts; Next.js upgrade; cost visibility; real-speaker
voice test.

## 15. Production-readiness assessment

| Gate | Status |
|---|---|
| Demo to the guide | **Ready with care** — after P2-01 (badge), P1-05 (first chip), P1-09 (slides); avoid Hindi/Kannada hands-free calls and the phone line |
| Internal trial with staff test data | **Ready after P0-01, P0-03, P0-04, P0-08** |
| Pilot with real applicants | **Not ready** — needs all P0, accuracy ≥ 90% on ≥ 150 real questions, p95 ≤ 5 s, readiness passing, data-flow sign-off by the college's legal office, lab-server hosting with backups and an alert (PRD 10.3) |
| Production (Level 2) | **Not ready** — Level 2 scope, 99.5% availability, ASVS L2, DPIA |

## 16. Changes made during this review (disclosure)

The review was conducted as an audit. For transparency, everything it changed:

| Change | Detail | Reversible |
|---|---|---|
| Ten review reports and this report | new files in `docs/`, uncommitted | delete the files |
| `docs/proposed-contracts/*.json` regenerated | side effect of running `scripts/check_contracts.py` in phase 1 (the script rewrites these files from the code); 1,444 lines added, 106 removed — they now match the current code | `git checkout docs/proposed-contracts` |
| Test conversations in the local database | **125** conversations with ids `eval-probe-…` (254 messages) from the test-set, latency, injection and memory runs; **10** other conversations and **15** widget sessions from the widget, push-to-talk and isolation tests. Nothing was deleted or overwritten. | can be removed by id prefix on request |
| Tunnel address re-registered | after each Mac reboot, `scripts/voice_tunnel.sh` pointed the Retell agent and the Vapi number at the new tunnel address (needed for voice to work) | runs again on each restart |
| Memory note | the Mac's test limit recorded so `test_engine_db.py` is not run here again | — |
| Test tools | `puppeteer-core` and `axe-core` installed in the session's scratch folder only, not in the project | — |

Not changed by the review: application code, database schemas, migrations, configuration files,
existing test data, git history. No paid service was activated and nothing was deployed.

Before the review (earlier on 18 Nov, at the user's request, still uncommitted): the Retell agent's
background "mm-hmm"s were turned off and its voice changed to ElevenLabs `11labs-Monika`
(`assistant/scripts/retell_agent.py`, `assistant/tests/test_retell_agent.py`,
`assistant/README.md`).

## 17. Next step

No recommended change has been implemented. On approval, the work would proceed incrementally,
one roadmap item per change, each with its tests run (within the Mac's limits) and existing
behaviour preserved, starting with the week-1 items: P0-01, P0-03, P0-04, P0-05, P1-05, P1-09,
P2-01.

## 18. Corrections after the independent re-check (19 Nov 2026)

Re-checking this report against the system found five claims that were wrong or overstated:

| Claim in this report | What re-checking showed |
|---|---|
| "Accuracy 50%" | That scored whether the expected curated *source* was cited. Scored on the answer itself: 30% of in-scope questions were declined, asked "Did you mean…?" or handed off; of 15 answers with a checkable number or code, 9 had it, 6 omitted it, **none stated a wrong one**. The measured problem was refusals and omissions, not invented facts. `scripts/eval_answers.py` now scores answers directly. |
| "Half of Retell calls fail" | 24 of the 28 connection failures were on 13 Nov, the set-up day. Since 17 Nov: 15 normal calls, 2 connection failures. The "never joined" calls on 18 Nov were created by this review's own push-to-talk test (now fixed: a Retell call is created only for a hands-free call). |
| Super-admin reads raw feedback (critical) | Row-level security limits the account to its own college; it cannot reach RUAS. The issue is narrower (medium): a platform account attached to one college saw that college's comments. Fixed: redacted unless break-glass is active. |
| "8 sources in every prompt" | Module B puts 5 sources in the prompt (`max_sources = 5`) and searches for 6. |
| "Staff cannot fix knowledge; the Knowledge page is read-only" | The programme list is read-only **by design**: a fee changes through "Correct this answer", which is approved and tested before it goes live (PRD 8.3). What staff cannot do is *add* a missing fee, date or eligibility, upload a document or re-crawl. |

Missed by this report and found on re-check: e-mail cannot be sent (no SMTP server; 10 messages waiting); RUAS has never had a handoff, lead or consent; six RUAS curated answers (scholarships, hostel, placements) were imported on 13 Nov as drafts and never approved; the database accepts local connections without a password and FileVault is off; and the repository already had an evaluation runner (`scripts/eval_live.py`).
