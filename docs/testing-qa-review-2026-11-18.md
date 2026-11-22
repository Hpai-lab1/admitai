# Testing and quality-assurance review (18 Nov 2026)

Scope: every automated test in the repository (Modules A, B, C and the web app), the end-to-end
scripts, CI, and the manual and live checks made during this review (phases 2–8). Every result
below comes from a run on 18 Nov 2026 on the local Mac (8 GB, Apple Silicon), against the
throw-away test database `admitai_local_test`. Anything not run is marked **not run**, with the
reason.

## 1. Results of this run

| Suite | Files | Result | Time | Notes |
|---|---|---|---|---|
| Module C (Python) | 3 | **124 passed** | 11 s | database tests included (none skipped) |
| Module A, without database | 8 | **63 passed, 4 skipped** | 1 s | skipped: OCR — Tesseract not installed (3), no TrueType font for the test scan (1) |
| Module A, database | 12 | **120 passed, 2 skipped** | 13 s | skipped: pgvector not installed (2) |
| Module B, without database | 32 | **375 passed** | 17 s | run in four batches |
| Module B, database (6 of 7 files) | 6 | **77 passed** | 19 s | did-you-mean, multilingual, voice audio, LiveKit, WhatsApp consent, WhatsApp |
| Module B, `test_engine_db.py` (62 tests) | 1 | **not run to completion** | — | see §2 |
| Web unit tests (vitest) | 30 | **107 passed** | 8 s | two workers |
| Web type check (`tsc --noEmit`) | — | **0 errors** | | |
| Web lint (`eslint src`) | — | **clean** | | |
| **Total** | | **866 passed, 6 skipped, 0 failed; 62 not run** | | |

## 2. A test file that cannot run on the development Mac

`assistant/tests/test_engine_db.py` (62 tests: engine turns, service-key scopes, visitor tokens,
production checks, rate limits, model policy, dropped streams, injection and more) drove free
memory below 10%. The first attempt was killed by the system after 43 tests (exit 137); a second
attempt in two halves ended with the **Mac rebooting** (all services and the database went down
and were restarted). The single test at the crash point passes alone in 0.14 s, so the cause is
memory growing across the file, not one test.

Combined with §3, **these 62 tests currently run nowhere**: not in CI (no database) and not on the
team's 8 GB laptops. They include the engine's core behaviour and several security checks.

**Fix (P0):** run the database tests in CI with a PostgreSQL service; find the growth in
`test_engine_db.py` (likely app instances or model clients created per test and kept alive by
module-level caches) and split the file.

## 3. Continuous integration

`.github/workflows/ci.yml` runs pre-commit (format, lint, secret scan), checks that the contract
files parse, and runs `pytest -q`. It has **no PostgreSQL service**, so every database test skips
in CI (`needs_db` markers: 20 files across A, B and C), and it **does not run the web checks**
(type check, lint, vitest). A green CI run therefore proves only the unit tests.

**Fix (P0):** add a `postgres:14` service with pgvector, run the migrations, and run all database
tests; add a web job (`npm ci`, `tsc --noEmit`, `eslint`, `vitest run`). Optionally install
Tesseract in the CI image so OCR tests run.

## 4. Coverage by kind of test

| Kind | What exists | Ran in this review | Gap |
|---|---|---|---|
| Unit | ~560 Python tests, 107 web tests | ✅ all passed | coverage percentage never measured (`pytest-cov` not installed; PRD asks ≥ 70%) |
| Integration (database) | 20 `*_db` files using a throw-away database, RLS and real SQL | ✅ 19 of 20 files passed; `test_engine_db.py` not run | none of them run in CI |
| API | FastAPI TestClient in most route tests; contract presence check (`check_contracts.py`) | ✅ | contracts cover 17 of 167 routes, so contract tests cover little |
| Frontend | vitest on components and helpers (widget, call panel, answer text, roles, feedback) | ✅ 107 passed | **no tests for console pages** (approvals, knowledge, analytics, admin); no browser tests |
| End-to-end | `scripts/e2e_local.py` (walking skeleton), `e2e_channels.py`, `e2e_learning.py`, `e2e_webhooks.py`, run against live services | **not run** — they write consents, leads and callbacks into the real local database; need the user's agreement or a separate database | not in CI; no record of their last run |
| RAG evaluation | RUAS test set (72 cases) and the gate's suites | ✅ live run in phase 2: **50% pass**; retrieval Hit@1 / @4 / @8 **48% / 70% / 74%** (phase 6) | not run automatically; ≥ 150 real questions needed; out-of-scope cases out of date |
| Hallucination / grounding | sentence-check unit tests (`test_fact_binding`, `test_rules`, checks), translation `same_facts` tests | ✅ | no test for citation markers used as words ("[1] and [2] all mention…"); no cross-language consistency test (a Hindi and a Kannada answer contradicted each other) |
| Voice | Retell, Vapi, Sarvam, spoken-lead, voice audio and LiveKit unit and DB tests | ✅ passed | **all against fakes**: the one real Vapi call failed at the step a test covers (`test_an_inbound_call_gets_the_saved_assistant_with_this_engine_answering`) — the fake request does not match Vapi's real one; half of the 74 real Retell calls failed on connections; real-speaker accuracy never measured |
| Multilingual | `test_language.py`, `test_multilingual_db.py` (25), language detection and decision tests | ✅ passed | live Hindi 5/10 and Kannada 9/16 real answers — the tests check the mechanics, not answer quality |
| Security | service-key scopes, visitor tokens, production checks, rate limits (in `test_engine_db.py` — **not run**); RLS per module; regex-safety (`test_regex_linear`); webhook signatures | partly | masking tests have **no OTP or name cases** (the leak in phases 3–4 went unnoticed); no test that a super-admin gets aggregates only; no SSRF test for the crawler; prompt-injection cases exist only in self-learning and the not-run engine file |
| Regression | the evaluation gate re-runs suites before a knowledge change goes live | ✅ in the product | gate runs used 7–8 cases; no regression run of the full RUAS set per release |
| Performance | latency benchmarks from 13 Nov (`assistant/benchmarks/`) | measured ad hoc in phases 2, 5–7 | no load test (PRD: 20–40 chats, 5 voice sessions) |
| Accessibility | axe-core scan (phase 3: 0 violations on 14 pages); manual script `docs/accessibility-test-script.md` | ✅ automated | manual keyboard / screen-reader pass not repeated since the call panel and comment box were added |

## 5. Features that appear to work but have no reliable test

| Feature | Why the current tests are not enough |
|---|---|
| Engine turn flow, service-key scopes, production checks, rate limits | their 62 tests run neither in CI nor on the laptops (§2–3) |
| Telephone calls (Vapi) | tests pass with a fake request; the only real call failed |
| Retell calls end to end | WebSocket logic is unit-tested; the connection path (tunnel, reconnect) is not, and half of real calls failed there |
| Personal-data masking | tests miss OTPs and names, which leaked in practice |
| Hindi and Kannada answer quality | mechanics tested; real pass rates 50% and 56% |
| Approval inbox, knowledge page, analytics, admin pages (console) | no page tests; the problems found in phases 3–4 (non-questions in the inbox, PII on analytics, unconfirmed role changes) are untested behaviour |
| Super-admin data access | no test that feedback is aggregated for the platform role |
| Retention | `retention_sweep` is unit-tested, but nothing schedules it and no test checks that it runs |
| OCR of scanned PDFs | tests skip here (no Tesseract) |
| pgvector search path | tests skip here (no pgvector) |
| Website crawl against the real site | `test_site_kb.py` uses saved pages; no check that a fresh crawl still finds fees, dates and eligibility (the readiness gaps) |
| Widget on a phone, voice call panel in a browser | no browser tests |

## 6. Prioritised testing plan

### P0 — before the next review or any real applicant

| # | Action | Done when |
|---|---|---|
| 1 | **CI with PostgreSQL + pgvector** running every `*_db` test, and a **web job** (tsc, eslint, vitest) | all 20 database files and the web checks run on every pull request |
| 2 | **Fix the memory growth in `test_engine_db.py`** and split it into topic files | the file runs on an 8 GB laptop with > 30% memory free |
| 3 | **Masking test cases** for OTPs, PINs and names, shared by A and B | a test fails before the fix and passes after |
| 4 | **RUAS evaluation as one command** (`scripts/eval_ruas.sh`): answer set, retrieval Hit@k, injection probes, latency per language; dated report | report committed before each demo; target pass rate tracked |
| 5 | **Refresh the RUAS test set**: update out-of-scope cases that the website now answers; add the Hindi/Kannada contradiction pair; add 80+ real questions from the admissions office | ≥ 150 real cases (PRD F10.3) |
| 6 | **Record a real Vapi request** and make the inbound test use it; one completed test call | a phone call is answered end to end |

### P1 — during the pilot

| # | Action | Done when |
|---|---|---|
| 7 | Measure coverage (`pytest-cov`) per module in CI | numbers on every PR; PRD target ≥ 70% |
| 8 | Run the e2e scripts against a **separate e2e database** in CI (or nightly) | `e2e_local`, `e2e_channels`, `e2e_learning`, `e2e_webhooks` green nightly |
| 9 | Console page tests: approvals (dismiss non-questions, publish-now admin only), knowledge (pagination), admin (role change confirmation), analytics (no PII) | one test per finding in the console review |
| 10 | Security tests: super-admin aggregates only; crawler refuses private addresses; widget session refused from an unlisted origin; failed sign-ins audited | each a failing test before its fix |
| 11 | Voice: 20 recorded real questions per language (consented) replayed through push-to-talk; word error rate and answer correctness reported | numbers in the voice review table |
| 12 | Load test: 30 simulated chats and 5 voice sessions for 10 minutes | p95 latency and error rate recorded (PRD 10.2) |
| 13 | Browser tests (Playwright) for the widget on desktop and phone width: ask, answer with citation, talk to a person, consent | runs nightly |

### P2 — later

| # | Action |
|---|---|
| 14 | Install Tesseract in CI so OCR tests run |
| 15 | Fresh-crawl check of the RUAS website weekly: fees, dates and eligibility found for every programme |
| 16 | Repeat the manual keyboard / screen-reader script on the call panel and comment box |
| 17 | Cross-language consistency suite: the same 20 questions in English, Hindi and Kannada must give the same facts |

## 7. What was not run, and why

| Item | Reason |
|---|---|
| `assistant/tests/test_engine_db.py` | exhausts memory on the 8 GB Mac; the second attempt rebooted the machine |
| `scripts/e2e_*.py` | they write test consents, leads and callbacks into the real local database; not run without the user's agreement |
| OCR and pgvector tests | Tesseract and pgvector not installed on this Mac (skipped by the suites themselves) |
| Coverage percentage | `pytest-cov` not installed; not added in this review |
