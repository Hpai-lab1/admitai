# Prioritised improvement roadmap (18 Nov 2026)

This roadmap brings together every issue found in the ten reviews of 18 Nov 2026 (all in `docs/`,
named `*-2026-11-18.md`), removes duplicates, and classifies each one:

| Priority | Definition used |
|---|---|
| **P0 – Critical** | security vulnerability, risk of losing or leaking data, or core functionality broken |
| **P1 – High** | major PRD gap, incorrect answers, or a broken workflow |
| **P2 – Medium** | performance, usability or maintainability |
| **P3 – Low** | cosmetic refinements and future enhancements |

Complexity: **S** = under a day · **M** = 1–3 days · **L** = a week or more (one developer).
Source reports: PRD = `prd-traceability`, UX = `frontend-ux-review`, CON = `staff-console-review`,
BE = `backend-review`, AI = `ai-rag-review`, VO = `voice-review`, SEC = `security-privacy-audit`,
QA = `testing-qa-review`, 3O = `third-order-review`.

Counts: **P0 10 · P1 22 · P2 30 · P3 12** (74 items).

---

## P0 – Critical

| ID | Problem | Evidence | Why it matters | Recommended solution | Expected benefit | Cx | Dependencies | Testing requirements |
|---|---|---|---|---|---|---|---|---|
| P0-01 | Two provider API keys (speech, telephony) were pasted into a chat session and not rotated | set-up conversation; values not in git or logs (SEC) | Anyone with that chat history can use the accounts | Rotate both in the providers' dashboards; update the local secrets file; remove the unused `BOLNA_KEYS` | Closes credential exposure | S | Provider dashboard access | Old keys rejected by the providers; app works with new keys |
| P0-02 | Voice providers receive raw voices and transcripts without the college's opt-in | `routes/voice.py:98`, `sarvam.py:144`, `vapi.py:106` switch on by key presence; nothing masked (SEC C1, VO §9) | Breaks PRD privacy rules (no external calls in L1; opt-in, redaction, DPA in L2) and DPDP; under-18 users | Per-college switch in `model_policy` (like `external: gemini`), default off; Retell recording/storage off; notice before a call | Lawful processing; college controls its data | M | Data-processing agreements; guide decision (PRD clarification T6) | Unit: college with voice off makes no provider call; agent config shows recording off |
| P0-03 | Personal-data masking misses one-time codes and names | `guard.mask_pii` and A `pii.py` leave "My OTP is 482913" and "marks of Rahul" untouched (verified); shown on Analytics and Approvals; same text sent to Gemini (SEC C2, UX U3) | Leaks applicants' and third parties' data to staff screens and to Google | One shared masking function and test file for A and B; mask codes near OTP/PIN/password and names after "my name is / of / for"; hide flagged questions from Analytics and Approvals | Masking matches its promise | M | — | Shared cases (OTP, PIN, names, Indian phone formats) fail before, pass after; Analytics shows no unmasked sample |
| P0-04 | Super-admin reads a college's raw feedback and comments without break-glass | live: `GET /feedback` as super-admin → 200 with comments (CON R1, SEC C3) | PRD 5.3: platform staff see aggregates only | Aggregates for `super_admin` unless break-glass is active; audit reads during break-glass | Enforces tenant data boundary | S | — | Role test: super-admin gets counts only; with break-glass gets rows and an audit entry |
| P0-05 | Retention rules never run | `retention_sweep` (B, C) and `purge_old_audit` are command-line only; no cron, launch agent or deploy job (SEC H3) | Data kept beyond configured retention (PRD 11.4, DPDP) | Run daily from the services' background loops; show "last retention run" on Admin | Data deleted on schedule | S | — | Integration: old rows removed after a sweep; scheduler test; Admin shows the time |
| P0-06 | Lost leads and consent records when Module C is down | B→C calls synchronous, no retry: spoken number dropped (`voice_turns.py:200`), WhatsApp opt-in recorded as not saved while the chat continues (`whatsapp.py:824`) (BE B2) | Data loss; conversations without a stored consent | Outbox table in B with claim-and-retry (Module C's existing pattern) | No lost leads or consents | M | — | Integration: C stopped → item queued → C restarted → delivered once |
| P0-07 | Running services hold a superuser database connection | `gate.py:576–1717`, `learning.py:745`, `webhooks.py:203`, `webpush.py:117` use `postgres` at run time (SEC H1) | Any code-execution bug in A or C gives full control of every college's data | `admitai_worker` role with `BYPASSRLS` and grants only on the tables those jobs touch; superuser for migrations only | Limits blast radius | M | Migration for the role and grants | All A/C database tests pass with the worker role; superuser absent from runtime config |
| P0-08 | The engine's database tests run nowhere | CI has no PostgreSQL (20 `*_db` files skip); `test_engine_db.py` exhausted memory and rebooted the 8 GB Mac (QA §2–3) | Core engine and security checks can break with a green CI | CI job with PostgreSQL + pgvector running every database test; web job (tsc, eslint, vitest); split `test_engine_db.py` and fix its memory growth | Every merge verified | M | GitHub Actions minutes | CI shows all 20 database files and the web checks; engine tests run on an 8 GB laptop |
| P0-09 | Telephone calls do not work | Vapi's log: one call ever, ended `assistant-request-returned-error` (VO §2) | Advertised channel broken | Capture a real Vapi request, log the webhook response, fix, and replace the test fixture with the recorded request | A working phone line, or an honest "in integration" label | M | Vapi number; stable public address (P0-10) | Fixture from a real request; one completed call per language |
| P0-10 | Half of hands-free website calls fail | Retell log: 37 of 74 calls ended in errors — 12 `error_llm_websocket_open`, 16 `_runtime`, 9 user not joined; call registry in memory (`retell.py:39`) (VO §2, BE B1) | Core voice channel unreliable | Stable address (named tunnel or lab server); call registries in the database; reconnect handling | Calls survive restarts and address changes | M | Lab server or Cloudflare account | 20 test calls with 0 connection errors; restart during a call does not drop it |

## P1 – High

| ID | Problem | Evidence | Why it matters | Recommended solution | Expected benefit | Cx | Dependencies | Testing requirements |
|---|---|---|---|---|---|---|---|---|
| P1-01 | Answer accuracy 50% against the PRD's ≥ 90% | live run of 72 RUAS cases: 36 pass (PRD §test-set) | The pilot's first acceptance criterion | P1-02 to P1-07 below; re-run after each | Measurable path to the target | L | P1-02…P1-07 | Full RUAS set after each change; report kept |
| P1-02 | The website FAQ page duplicates all curated answers | 48 FAQ chunks; website copy is top-1 in 30 of 54 searches; 13 answers cite the copy (AI §2–3) | Corrections to curated answers do not reach visitors | Index the curated answers only, or mark the FAQ page as superseded | Corrections take effect | S | — | Retrieval run: curated answer top-1 ≥ 80% for its own question |
| P1-03 | 32% of chunks are fragments; some under the wrong heading | median 343 characters; 462 of 1,445 under 200; "12 Seats" under *Elective Courses* (AI §2) | Noise in retrieval; numbers attached to wrong context | Merge sections to ~300–400 tokens; drop chunks under 60 characters; never chunk a bare number | Cleaner retrieval; fewer misattributed facts | M | Re-crawl | Chunk stats: < 5% under 200 characters; retrieval Hit@1 not lower |
| P1-04 | No question variants on any curated answer | 0 of 214 (AI §2) | Paraphrases miss; "What is the application fee?" not in top 8 | Generate 3–5 variants per answer with the existing drafting model; staff review in the console | Paraphrase Hit@1 65% → ≥ 85% | M | P1-12 (console) for review | Retrieval run before/after |
| P1-05 | The widget's first suggested question fails | "What is the application fee?" → "Did you mean…?" (PRD run r01, CON) | First thing anyone clicks in a demo | P1-02 and P1-04; until then change the starter chip | Reliable first impression | S | — | Each starter chip answered with a citation (automated check) |
| P1-06 | Hindi and Kannada answers weak and slow | real answers 5/10 and 9/16; 3–4 model calls per turn; median 12–16 s (AI §6, PRD) | Inclusion goal; PRD F1.6 | Translate approved curated answers and catalogue facts once at approval (checked by `same_facts`); skip the English rewrite when bge-m3 retrieval is confident | ≤ 2 model calls; Indian-language median ≤ 5 s | L | Staff review of translations (console) | Test set by language; calls per turn from traces |
| P1-07 | Latency p95 23.9 s against ≤ 5 s | live run (PRD NFR); Gemini first-token wait set to 15 s in `local.env.sh` | Visitors leave; PRD NFR | `GEMINI_FIRST_TOKEN_S=6`; 4 sources instead of 8 (Hit@4 ≈ Hit@8); related questions after the answer; paid key (P1-08) | p95 toward 5 s | M | P1-08 | Latency per language in the eval report |
| P1-08 | The answer model is a free-tier key; three conflicting model stories | throttling seen (15 s timeouts after bursts); PRD names lab models; slides claim a self-trained 13–15B model (AI §1, 3O 1.3) | Unpredictable in season; examiners and colleges cannot plan | Decide: paid Gemini under an agreement for opted-in colleges, lab 14B as default; document it in README, PRD notes and slides | One plan for cost, privacy, quality | S (decision) / M (lab wiring) | Guide decision; lab server | Configuration check at start-up; eval run on the chosen model |
| P1-09 | Presentation does not describe the system | slides 9, 10, 14: training a 13–15B model, FAISS/Chroma, LangGraph, HPC request (AI §1) | Credibility at review | Rewrite the methodology slides: checked RAG over the college's own data, hosted model with local fallback, pgvector, explicit pipeline, no training | Accurate review | S | — | Guide reads slides against `ai-rag-review` §1 |
| P1-10 | The college's data is incomplete | readiness: fees missing for 9 programmes, dates for ~40, eligibility for 1, owners for most pages; 73 of 150 real test questions (PRD) | Launch gate fails by the system's own rules | Import the admissions office's fee and dates sheet; auto-assign owners per page type | Readiness passes | M | Admissions office data | Readiness endpoint shows all blocking checks passing |
| P1-11 | Staff cannot manage knowledge in the console | Knowledge page read-only; A has upload, sources, fee edit, document edit, curated edit APIs with no screens (CON §1) | Every content fix needs a developer; PRD 5.3 gives staff these rights | Knowledge page tabs: Programmes (edit fees/dates), Documents & website (upload, re-crawl, owner, validity), Curated answers (edit, pause), Test questions | Staff own their content | L | P1-13 (crawl proposals) | Console page tests per action; permission tests per role |
| P1-12 | Website changes go live without review | nightly crawl stores pages as approved and replaces the catalogue (`site_kb.py`, `pack.py:110`) (3O 1.1) | Website typos become cited answers; inverts PRD ownership | Crawl produces a "what changed" list approved by the owner; fees and dates never auto-published | Staff-approved facts | M | P1-11 | Crawl with a changed fee → proposal created, catalogue unchanged until approved |
| P1-13 | Approval inbox full of non-questions; dismissing takes several steps | injection, OTP, other students' marks, "guarantee a seat" as "missing knowledge"; reject only via batch bar; 7,400-px page (UX U4, CON) | Wastes staff time; staff stop using it | Classify before proposing (reuse `selflearn/classify.py` rules); collapsed "not answerable by design" group; per-card Reject / Not a gap; one-line rows; drafts pre-filled | Inbox cleared in minutes | M | P0-03 | Page tests; non-questions never create proposals |
| P1-14 | No password reset; temporary passwords never changed | `UserPatch` has only `reset_mfa`; no forced change (CON, SEC H2) | Locked-out staff need a developer; long-lived temporary passwords | Forced change at first sign-in; admin "reset password"; later an e-mailed link | Account hygiene | M | E-mail sending for the link | Auth tests: first sign-in forces change; reset issues a one-time password |
| P1-15 | Test set out of date and too small | out-of-scope cases now answerable; 73 real questions vs ≥ 150; Hindi/Kannada contradiction untested (PRD T3, QA) | The gate fails correct behaviour and misses real errors | Refresh cases; add 80+ real enquiries; add consistency pairs; `scripts/eval_ruas.sh` producing a dated report | Trustworthy accuracy numbers | M | Admissions office questions | The script runs end to end; report in the repository |
| P1-16 | WhatsApp channel not connected | no token; tested only with signed fake webhooks (PRD F1.11) | PRD L1 channel | Meta test number, approved templates, one end-to-end test with a staff phone | L1 channel complete | M | Meta Business account | Live round trip: message, answer, buttons, opt-out |
| P1-17 | A college's widget can be embedded anywhere | public key in the embed script; no origin check; `frame-ancestors *` (SEC H5) | Impersonation; quota and cost abuse | Allowed origins per college; check on `POST /widget/session`; matching `frame-ancestors` | Widget only on the college's sites | S | Pack field | Session from an unlisted origin refused |
| P1-18 | Crawler fetches any address | no start-URL check; redirects to any host (`crawl.py:35`, `site_kb.py:211`) (SEC H4) | SSRF once staff can add sources (P1-11) | Public `https` only; refuse private, loopback, link-local after every redirect | Safe source management | S | Before P1-11 ships | Unit: private and redirecting URLs refused |
| P1-19 | Contracts cover 17 of 167 routes | route comparison; `check_contracts.py` checks one direction (PRD T2) | Project rule "the contract wins" cannot be applied | Adopt `docs/proposed-contracts/`; check both directions in CI | Modules can change safely | M | Guide review | CI fails on an undocumented route |
| P1-20 | Next.js 16.4.3 has a known vulnerability | dev-server advisory; Dependabot 16.4.5 waiting on the guide's repo (BE §10) | Known security issue in the public site | Upgrade on a branch | Patched framework | S | — | Web tests, type check, lint, manual smoke of widget and console |
| P1-21 | No staff-facing provider cost | 5,500 input tokens per Gemini answer; ×3 for Hindi/Kannada; per-minute voice charges untracked (3O 1.11, PRD F9.4) | Colleges cannot budget | Record tokens and minutes per turn and call; monthly estimate per college on the platform page | Predictable cost | M | Agreed prices | Usage totals match provider dashboards for a test day |
| P1-22 | Real-speaker voice accuracy never measured | only synthetic round trips (VO §4) | Voice claims unproven | 20 consented real questions per language; word error rate and answer correctness | Evidence for voice claims | M | Volunteers, consent | Report per language |

## P2 – Medium

| ID | Problem | Evidence | Why it matters | Recommended solution | Expected benefit | Cx | Dependencies | Testing requirements |
|---|---|---|---|---|---|---|---|---|
| P2-01 | Development badge on every page | "N 1 Insight" over headings, widget and sign-out (UX U1) | Unprofessional in a demo | Production build for demos or `devIndicators: false` | Clean screens | S | — | Screenshot check |
| P2-02 | Long waits shown only as "…" | 24.6 s with no status (UX U2) | Visitors think it froze | Status line after 2 s and 8 s; offer a person after 15 s | Visitors wait or hand off | S | — | Widget test with a slow stream |
| P2-03 | Knowledge page 29,771 px; lists unpaginated | `/curated-qa` 136 KB, `/kb/documents` 56 KB (UX U5, BE B5) | Unusable page; grows with content | `{items, next}` pagination on all lists; 50 per page; filters | Fast, usable lists | M | — | API pagination tests; page test |
| P2-04 | Console unusable on a phone | navigation cut at "Ap…", no sign-out (UX U6) | Staff on mobiles | Scrollable nav or menu sheet with account links | Mobile access | S | — | Phone-width screenshot test |
| P2-05 | Widget not full-screen on phones; launcher overlaps | 390-px screenshots (UX U7) | Cramped on the main device of applicants | Full-screen under 480 px; hide launcher while open | Better mobile chat | S | — | Phone-width test |
| P2-06 | Widget header truncated; consent link shown too early | "RUAS Admission…"; "Withdraw consent" before consent (UX U8–U9) | Confusing first view | Compact language menu; show withdraw only after consent | Clearer widget | S | — | Component tests |
| P2-07 | Role change and switch-off without confirmation | `admin/page.tsx:102` (CON, SEC M5) | Accidental access changes | Save button or confirmation | Fewer mistakes | S | — | Page test |
| P2-08 | Failed sign-ins not audited | rate-limited only (SEC M4) | Guessing invisible | Audit `auth.login_failed` with a hashed address | Detectable attacks | S | — | Auth test |
| P2-09 | Analytics mixes test traffic; shows model names to staff; preview chats count | eval and `console_preview` conversations counted (UX U11, CON) | Misleading numbers | Exclude both from all counts; move model usage to platform page | Accurate analytics | S | — | Analytics tests with mixed data |
| P2-10 | Review answers shows raw ids, no sources | "Conversation fac396d7-…" (CON) | Staff cannot judge the cause | Link to the conversation; show cited sources | Faster correction | S | — | Page test |
| P2-11 | Same dashboard for every role | Queue page for all (CON) | Admin and management see nothing actionable | Per-role "today" content | Daily efficiency | M | P1-11 | Per-role page tests |
| P2-12 | New roles without workflows | finance and hostel see empty lists; not in PRD (CON R3, 3O §2) | Role sprawl | Keep roles with a real queue; document kept roles in the PRD | Simpler permissions | S | Guide decision | Role tests |
| P2-13 | Process-memory rate limits in B | `guard.py:105` (BE B1) | Wrong with 2+ workers | Database-backed like Module C | Correct limits at scale | S | — | Two-worker test |
| P2-14 | Silent failures in Module A recovery | `main.py:40–52`, `gate.py:1633` `except … pass` (BE B4) | Problems invisible | `log.exception` kept non-blocking | Diagnosable failures | S | — | Unit test asserts a log record |
| P2-15 | No structured logs | plain uvicorn logs; `print` in service code (BE §8) | Hard to trace incidents | JSON logging with trace and tenant ids | Faster incident analysis | S | — | Log-format test |
| P2-16 | No alerts or monitoring | none found (PRD NFR) | Outages unnoticed | One alert: error rate, latency, failed nightly crawl (e-mail) | Early warning | M | Lab server | Simulated failure sends the alert |
| P2-17 | No `/v1` API prefix | all routes unversioned (BE B8) | Breaking changes hurt colleges later | Add `/v1` with old paths kept for one release | Safe evolution | M | P1-19 | Contract tests on both paths |
| P2-18 | Untyped tenant settings in B | `PUT /tenant-settings` takes `dict` (BE B7) | Bad pack fields fail at answer time | Pydantic model allowing extras | Earlier errors | S | — | Invalid settings rejected |
| P2-19 | Engine turn function ~650 lines | `engine.py:244–894` (BE §10) | Risky changes | Split into named steps without behaviour change | Easier maintenance | M | P0-08 (tests running) | Existing engine tests unchanged and green |
| P2-20 | Search cache stamp runs five count queries per search | `search.py:41` (BE §5) | Cost grows with content | Per-tenant version counter | Constant-time freshness check | S | — | Cache test |
| P2-21 | pgvector and Tesseract missing locally | tests skipped (QA) | Server path untested | Install on the server image and in CI | Paths verified | S | Lab server / CI | pgvector and OCR tests run |
| P2-22 | Keyword search English-only | `to_tsvector('english')` (AI §3) | Indic and transliterated words never match | Add a `simple` configuration | Kannada Hit@4 toward 80% | S | — | Retrieval run by language |
| P2-23 | No "sources are data" rule in the prompt | `prompts.SAFETY_RULES` (AI §5, SEC M2) | Indirect injection via crawled pages | Add the rule; add injection probes to the test set | Defence in depth | S | P1-15 | Probes in the eval report |
| P2-24 | Citation markers pass as words | "[1] and [2] and [3] all mention that…" (UX U13, AI §5) | Poor answers released | Sentence check rejecting citation-led sentences | Better wording | S | — | Unit test |
| P2-25 | Mixed questions lose their valid part | "IPL… also hostel fee?" declined entirely (AI §5) | Visitors lose answers | Drop off-topic sub-question in the router; answer the rest | Fewer needless refusals | S | — | Router test |
| P2-26 | Five voice paths to maintain | Retell, push-to-talk/Sarvam, Vapi, LiveKit, WhatsApp notes (3O 1.5) | Upkeep and privacy surface | Push-to-talk + one phone path for the pilot; park LiveKit; Retell optional | Less to break | S (decision) | Guide decision | — |
| P2-27 | Self-learning on locally at pilot volume | 2 feedback rows, 35 conversations in 30 days (3O 1.7) | Rarely acts; adds a second approval path | Off until volume justifies; keep code | Simpler operations | S | — | Config check |
| P2-28 | Missing security headers; API docs public through the tunnel | no CSP, `nosniff`, Permissions-Policy; `/docs` 200 via tunnel (SEC M1, M3) | Hardening | Headers in `next.config.mjs`; docs off outside localhost | Smaller attack surface | S | — | Header test; tunnel probe returns 404 for docs |
| P2-29 | No load test, coverage or browser tests | none in repo (QA) | Unknown capacity and gaps | 30 chats + 5 voice load test; `pytest-cov` in CI; Playwright for the widget | Known limits | M | P0-08 | Reports in CI |
| P2-30 | Accessibility not re-tested since the call panel and comment box | last manual run 9 Nov (UX U14) | WCAG 2.2 AA is a PRD target | Repeat the keyboard / screen-reader script | Compliance evidence | S | — | Script results recorded |

## P3 – Low

| ID | Problem | Evidence | Why it matters | Recommended solution | Expected benefit | Cx | Dependencies | Testing requirements |
|---|---|---|---|---|---|---|---|---|
| P3-01 | Default black 404 page | `/console/<unknown>` (UX U12) | No way back | Console-styled `not-found.tsx` | Polish | S | — | Page test |
| P3-02 | Missing favicon | `/favicon.ico` 404 (UX U17) | Polish | Favicon from the pack | Polish | S | — | — |
| P3-03 | Widget theme follows the system, not the page | dark widget on a light page (UX U15) | Visual mismatch | Pack option "follow page" | Consistent look | S | — | Screenshot |
| P3-04 | Widget header font differs from the site | Playfair vs Montserrat (UX U16) | Minor inconsistency | Use the site font unless deliberate | Consistency | S | — | — |
| P3-05 | Hero example questions look like buttons | demo site (UX U18) | Expectation mismatch | Make them open the widget with that question | Polish | S | — | Click test |
| P3-06 | README says audio is never stored | consented push-to-talk audio is stored (SEC M9) | Documentation wrong | Correct README and visitor notice | Accurate docs | S | — | — |
| P3-07 | Provider ids in logs | agent, tenant and number ids (SEC L1) | Low exposure | Log last four characters | Tidier logs | S | — | Log test |
| P3-08 | `LIKE` wildcards unescaped in lead search | `consents_leads.py` (SEC L4) | Odd results | Escape `%` and `_` | Correct search | S | — | Unit test |
| P3-09 | `app.rate_hits` RLS not forced | catalogue (SEC L3) | Consistency | Force it | Uniform policy | S | — | Migration test |
| P3-10 | Missing foreign keys on `chat.voice_audio`, `chat.wa_messages` | catalogue (BE §4) | Possible orphans | Add FKs with cascade | Integrity | S | — | Erasure test |
| P3-11 | Local speech stack always installed | `faster-whisper`, `piper-tts`, `livekit` in B requirements (BE §10) | Larger deployments | Optional requirements file | Smaller images | S | — | Install check |
| P3-12 | Tenant-first indexes absent on 32 tables | catalogue (BE §4) | Matters only at scale | Add `(tenant_id, created_at)` where lists are per tenant | Future performance | S | — | Query plans |

---

## Suggested order of delivery

| Week | Items | Outcome |
|---|---|---|
| 1 | P0-01, P0-03, P0-04, P0-05, P1-05, P1-09, P2-01 | Keys safe; masking and super-admin fixed; retention runs; demo-ready screens and slides |
| 2 | P0-08, P0-06, P0-07, P1-15, P1-02 | CI verifies everything; no lost data; least-privilege workers; trustworthy test set; duplicates removed |
| 3 | P0-02, P0-10, P0-09, P1-08 | Voice lawful and stable; phone path fixed or labelled; model decision made |
| 4–5 | P1-03, P1-04, P1-07, P1-10, P1-13, P1-14, P1-17, P1-18 | Retrieval and latency toward targets; data complete; inbox and accounts usable; widget and crawler hardened |
| 6–8 | P1-06, P1-11, P1-12, P1-16, P1-19, P1-21, P1-22 | Indian-language quality; staff own knowledge; WhatsApp live; contracts; cost visibility; real voice accuracy |
| Pilot | P2 items by need; P3 as polish | — |

**Exit criteria before real applicants** (from the PRD and these reviews): all P0 closed;
accuracy ≥ 90% on a refreshed RUAS set of ≥ 150 real questions; first-sentence p95 ≤ 5 s;
readiness checklist passing; CI running every database and web test; a data-flow page per college
signed off by its legal office.
