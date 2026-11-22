# Self-learning knowledge layer: discovery (Phase 1)

Branch `a/module-a-self-learning`, from `origin/main` at `3a01229` (18 Nov 2026). Nothing has been
built yet; this is what exists, what is missing, and the plan to confirm.

## 1. How feedback is captured today

**Path:** widget → Module C `POST /feedback` → `app.feedback` row and an `app.outbox_events` row
→ Module C delivers it to Module A `POST /events` (`feedback.created`, at-least-once) → stored in
`learn.events` (de-duplicated on `(tenant_id, event_id)`) → the nightly Feedback Analyst
(`learning.ingest`) turns it into a `learn.signals` row, filling in the question, answer and
citations from Module B's `GET /messages/{message_id}` and masking PII with `pii.mask_pii`.

| Field the task needs | Available to Module A? | Where it comes from |
|---|---|---|
| college id | yes | `tenant_id` on the event |
| conversation id | yes | event payload `conversation_id` |
| turn id | yes | event payload `message_id` (B's assistant message id) |
| rating | yes | `kind` thumb (±1) or rating (1–5); `reason` from a fixed list (`wrong`, `outdated`, `incomplete`, `not_what_i_asked`, `hard_to_understand`, `other`) |
| comment | partly | `comment` (≤1000 chars), but see §2 |
| question, answer | yes | B `GET /messages/{id}` (`question`, `answer`, `outcome`, `language`, `confidence`, `route`) |
| citations | yes | B `GET /messages/{id}` `sources[]`: `n`, `source_type`, `ref_id`, `title`, `heading_path` |
| retrieved chunk ids | **no** | B keeps only the *cited* sources. The trace (`GET /traces/{id}`, span `retrieval`) has the query and the cited sources' titles and scores, not the uncited candidates or chunk ids |
| channel | partly | `channel` is `web_chat`, `whatsapp` or `web_voice` only. Phone calls (Retell, and since 17 Nov Vapi) have no feedback path |
| session fingerprint / IP | **no** | Not in the payload. B's `chat.conversations.session_id` exists but is not returned by `/messages/{id}`. No IP address reaches Module A anywhere |
| trace id, department, actor | yes | `trace_id`, `department`, `actor` (`visitor`/`staff`) |

**Consequence for the agreement threshold:** without a session id, the only distinct-visitor unit
Module A can count is `conversation_id`. The widget makes a new conversation id when it opens
(`crypto.randomUUID()`), so one person can reload and appear as several "sessions". The trust
score compensates (similar wording, timing, the same message id across conversations is
impossible, and so on), but it cannot be proof. See §7, gap C1.

## 2. The comment box

It exists and its text is saved (`Widget.tsx` `Feedback`, `app.feedback.comment`), but **it is only
shown after thumbs-down → "Other"**. Picking "Wrong" or "Outdated" sends the reason with no text, so
a visitor cannot say *what* is wrong. The conversation rating (stars) has no comment box. Voice
calls have no feedback at all. These are Module C gaps (§7); I have not touched them.

## 3. Existing Module A pieces to reuse (not duplicate)

| Needed | Exists | Notes |
|---|---|---|
| Feedback store | `learn.events`, `learn.signals` (migrations 004–007) | Signals already hold question, answer, sources, comment, reason, channel, root cause, verdict |
| Feedback analyst | `learning.py`: `ingest`, `triage`, `propose`, `limit_bursts` | Deterministic triage, embedding clusters (cosine ≥ 0.80), impact score, approval SLA. Nightly, plus "at once" for staff corrections |
| Abuse resistance | `limit_bursts` (5 negative ratings per question per hour), verdict `suspected_abuse` | No per-session history, no injection or profanity detection |
| Approval inbox | `learn.proposals` + `GET /improvements`, `POST /improvements/{id}/approve\|reject\|rollback` | Module C's Approvals page (`experience_api/routes/approvals.py`, `proposals.tsx`) only proxies these and renders `kind`, `title`, `proposal`, `diff`, `examples` generically |
| Evaluation gate | `gate.gate_and_deploy`: sandbox copy → baseline → apply → candidate → `compare` | Metrics: accuracy, refusal, groundedness, forbidden-citation-free, routing; critical cases; the proposal's own new cases; retry of new failures; `select_quick` picks affected questions; `nightly_full_test`; one suite at a time (advisory lock), yields to visitors |
| Versions and rollback | `gate.apply_change` / `gate.rollback_change`; curated-answer versions (`curated.update`, `curated.rollback`); document versions; `published_early` / `taken_down` | Catalogue changes are re-imports, not versioned rows |
| Catalogue extraction | `site_kb.programme(page)`, `site_kb.fee_rows`, `kb.programs.source_url` (migration 009) | Values copied from the page, never generated |
| Crawler | `site_kb.discover` / `fetch_pages` (robots.txt, sitemap), `crawl.py` for `/kb/sources` | No "re-fetch one page" helper yet; it is built from the same functions |
| Hybrid search + reranker | `search.search` (vector + keyword, RRF), `rerank.py` (bge-reranker, memo) | `search.invalidate(tenant)` after any change |
| Paraphrase / alias expansion | `paraphrase.expand` (global admissions vocabulary); programme aliases in `kb.programs.aliases` | Global only: no per-college alias table |
| Curated Q&A | `curated.py` (versions, academic year, expiry, approval) | `apply_change` already creates or versions one |
| Drafting from documents | `drafting.py` (`POST /improvements/{id}/draft`): refuses a draft whose numbers are not in the cited documents | Reused for "curated Q&A copied from the source" |
| LLM access | `drafting.py` client (`KB_DRAFT_MODEL`, LiteLLM/Ollama) | Reused for the classifier, with a deterministic fallback |
| Retrieval metrics | `retrieval_eval.py` (Hit@1 / Hit@4 by mode) | **Not part of the gate today**; see plan §6.6 |

## 4. Reading conversation and feedback data without changing B or C

All of these already exist and are allowed:

1. **Events** Module C already sends to `POST /events`: `feedback.created`, `correction.submitted`,
   `signal.logged`, `erasure.requested`.
2. **Module B's service API** (`assistant_client.py`): `GET /messages/{id}`, `POST /messages/lookup`,
   `GET /signals`, `GET /reviews/unsupported`, `GET /traces/{id}`, and the headless `ask` used by
   the gate.
3. **No direct reads of `chat` or `app` tables.** The root `CLAUDE.md` forbids cross-module table
   access, so the read-only database role in the brief is not used.

## 5. How proposals reach the staff approval inbox

Module C's inbox *is* `learn.proposals`, read through `GET /improvements`. Anything Module A
inserts there with `status = 'pending'` appears in the inbox, and approve/reject come back as
`POST /improvements/{id}/approve|reject`. Approval already runs the gate in the background.
So routing to the inbox needs no Module C change, as long as each item uses an existing `kind`
(`curated_answer`, `document_action`, `synonym`, `prompt`, …) and carries its evidence in
`proposal`, `diff` and `examples`, which the inbox already shows.

## 6. Test sets and how the gate runs them

| Set | Where | Size |
|---|---|---|
| Demo college | `packs/demo-college/eval/questions.csv` | 26 |
| Riverside | `packs/riverside/eval/questions.csv` | 20 |
| RUAS hand-written | `packs/ruas/eval/questions.csv` | 72 |
| RUAS full set (373) | the 72 above **plus** cases generated from the catalogue (`catalogue_cases.py`, `learn.eval_cases.source = 'catalogue'`), regenerated on every crawl | ≈ 373 |
| Proposal cases | `learn.eval_cases.source = 'proposal'`, added when a proposal passes | grows |

`gate.load_cases` loads a pack's questions into `learn.eval_cases`. The gate answers the chosen
cases through Module B on a sandbox copy of the college (`clone_tenant`), grades them
deterministically (`gate.grade`), and `compare` decides. A full run of 373 cases takes tens of
minutes on the shared model, so a gate for one lesson finishes in **minutes, not seconds**: the
worker classifies within seconds, but going live waits for the gate.

## 7. What needs Module B or C (not done; each is a pull request for its owner)

| # | Module | Minimal change | Why |
|---|---|---|---|
| C1 | C | Add a salted hash of the visitor session id (`session_fp`) to the `feedback.created` payload, and, if allowed, a coarse network hash | The "3 distinct sessions" rule needs a real session; today a reload looks like a new visitor |
| C2 | C | Show the comment box for "Wrong", "Outdated" and "Incomplete" too (optional text), not only "Other" | Fact claims ("the fee is ₹X") cannot arrive otherwise |
| C3 | C | Allow `channel` values for phone calls (`retell`, `vapi`) and a feedback path after a call | Voice feedback does not exist |
| B1 | B | Record retrieved chunk ids (cited and uncited) on the message or trace | "retrieved_chunk_ids" cannot be filled; Module A can only re-run the search |
| B2 | B | Read an `answer_hints` field from `/kb/search` or `/curated-qa` and put it in the prompt | Without it, an "answer hint" lesson cannot change any answer (see decision D3) |

Everything else in the brief is possible inside `knowledge/`.

## 8. Conflicts with the project rules (decisions for you)

- **D1. Schema name.** Module A owns `kb` and `learn` only (`knowledge/CLAUDE.md`). I propose new
  tables in **`learn`** (`learn.feedback_events`, `learn.lessons`, …), not a new `learning` schema.
  The RLS pattern Module A uses is `kb.current_tenant()` with `tenant_isolation` policies
  (migration 002). `app.policy_role()` is Module C's role mapping and does not apply to Module A's
  tables.
- **D2. Going live without a person.** The PRD (8.4) and this module's README say *"Feedback alone
  never changes an answer: every change needs an approval and the gate."* The brief asks for
  evidence-verified lessons to go live automatically after the gate. Proposal: with the flag on,
  only **retrieval boosts and aliases** (no new text, nothing a visitor reads) and **curated Q&A
  copied word for word from an approved page with its citation** go live automatically; each still
  appears in the inbox as `deployed` with a one-click rollback. Catalogue re-extractions that change
  a number always go to the inbox. Please confirm, or ask for everything to wait for approval.
- **D3. Answer hints.** B has no input for them (B2). Until B adds one, `answer_style` lessons are
  sent to the inbox as the existing `prompt` proposals, as today. None go live automatically.
- **D4. Branch.** `a/module-a-self-learning` instead of `feature/…`, because the root `CLAUDE.md`
  requires the `a/` prefix.

## 9. Implementation plan (after confirmation)

### 9.1 Files (all under `knowledge/`)

```
db/migrations/010_self_learning.sql        tables below, RLS, grants, NOTIFY trigger
knowledge_api/selflearn/__init__.py
  config.py      flag + thresholds (read from Settings/env)
  classify.py    LLM classifier wrapper + deterministic fallback + injection/abuse rules
  trust.py       trust score per feedback and per fingerprint
  agree.py       agreement threshold (distinct trusted sessions per issue cluster)
  evidence.py    search the college's pages/docs, re-fetch one page, re-extract a programme
  draft.py       lesson drafting (alias, boost, curated Q&A from source, catalogue re-extract)
  gate.py        thin wrapper over gate.py: lesson -> sandbox -> retrieval + answer gate
  apply.py       go live as a knowledge version; rollback via gate.rollback_change
  monitor.py     live signals per lesson; auto-rollback rule
  worker.py      LISTEN/NOTIFY + poller, claim with SKIP LOCKED, retries
  report.py      counts for the report endpoint / script
knowledge_api/routes/selflearn.py          GET /learning/self/report (read-only)
search.py, paraphrase.py                   small hook: per-college aliases and boosts (no-op when flag off)
tests/test_selflearn_*.py                  unit, integration, adversarial
scripts/demo_self_learning.py
docs/self-learning/README.md, REPORT.md
```

### 9.2 Schema (`learn`, migration 010, RLS `tenant_isolation` on every table)

- `learn.feedback_events`: one row per `feedback.created` event (FK to `learn.events`),
  conversation/turn ids, rating, reason, masked comment, question, answer, citations,
  `retrieved_ref_ids` (B's citations, plus our own re-run search top-k, labelled as such), channel,
  `session_fp` (SHA-256 of tenant + conversation id + server salt, or C's hash once C1 lands),
  `status` (`new` / `processing` / `done` / `failed`), `attempts`, timestamps.
- `learn.feedback_analysis`: class, claim, topic/programme, confidence, model, short reasoning.
- `learn.trust_scores`: per feedback (score + factors) and per fingerprint (rolling history).
- `learn.lessons`: type (`alias`, `retrieval_boost`, `curated_from_source`, `catalogue_reextract`,
  `answer_hint`), status (`proposed`, `verifying`, `gated`, `live`, `rejected`, `rolled_back`,
  `pending_approval`), payload, `created_from` (feedback ids), `proposal_id` (inbox link),
  `knowledge_version` and `applied` (what `apply_change` did, for rollback).
- `learn.lesson_evidence`: URL / document id, exact quoted span, fetched_at, content hash.
- `learn.lesson_metrics`: gate before/after (accuracy, groundedness, refusal, Hit@1, Hit@4) and live
  thumbs ratio / repeat complaints by day.
- `learn.aliases` and `learn.retrieval_boosts`: per-college, versioned, read by search only when the
  flag is on and the row's lesson is `live`.

### 9.3 Worker

`POST /events` already stores the event. A trigger on `learn.events` sends `NOTIFY
learn_feedback`. A worker thread started in the app's lifespan (only when the flag is on)
`LISTEN`s, and also polls every `LEARNING_POLL_S` (default 5 s) as a fallback. It claims rows
with `update … where status = 'new' … for update skip locked` (one feedback processed once,
safe with several workers), retries with back-off up to 3 times, then marks `failed`. Each stage
writes one structured log line with `tenant`, `feedback_id` and `lesson_id`. The existing nightly
analyst keeps running unchanged, so both paths read the same events. The new path never writes
`learn.signals` or `processed_at`.

### 9.4 How each rule is enforced

| Rule | Enforcement |
|---|---|
| No B/C changes | Only `knowledge/` touched; checked with `git diff --stat main -- assistant experience contracts deploy` before every commit |
| Feedback is a signal | No lesson without a `lesson_evidence` row (except `answer_style`, which never goes live automatically); the comment text is never copied into knowledge |
| Numbers protected | `draft.py` cannot build a lesson with a number that is not in the quoted evidence span (same check as `drafting.py`); catalogue changes only via `site_kb.programme(page)` on a re-fetched page; a re-extraction that changes a fee, date, intake or mark always becomes `pending_approval`; test: "fees are ₹10" leaves `kb.program_fees` unchanged |
| No fine-tuning | Nothing calls a training endpoint; lessons are rows |
| Per college | Every table has `tenant_id` + RLS `tenant_isolation`; the worker opens `tx(..., tenant)` per feedback |
| Flag off = today | Worker not started, `/events` unchanged, search hooks return early; a test runs the existing search, events and gate tests with the flag off and compares outputs |
| No secrets | Salt and URLs from env; `.env.example` placeholders only |
| Agreement | Distinct `session_fp` with trust ≥ `LEARNING_MIN_TRUST` per issue cluster ≥ `LEARNING_MIN_AGREEING_SESSIONS` (3); 50 from one session count once |
| Gate | Lesson applied to a sandbox copy; the lesson's target questions plus a stratified sample from the college's test set (accuracy / refusal / catalogue cases); existing `compare` **plus** retrieval Hit@1/Hit@4 on the same cases (search-only, no model call); any regression blocks |
| Self-heal | `monitor.py` nightly and after each feedback: if the thumbs-down ratio on a lesson's questions rises above its before-level by `LEARNING_ROLLBACK_DROP`, or the nightly gate fails a case the lesson touches, call `rollback_change`, mark `rolled_back`, log the reason |

### 9.5 Configuration (all default to "off" or conservative)

`KNOWLEDGE_SELF_LEARNING_ENABLED=false`, `LEARNING_MIN_AGREEING_SESSIONS=3`,
`LEARNING_MIN_TRUST=0.6`, `LEARNING_POLL_S=5`, `LEARNING_SESSION_SALT` (required when on),
`LEARNING_AUTO_LIVE=retrieval_boost,alias,curated_from_source` (subject to D2),
`LEARNING_ROLLBACK_DROP=0.15`, `LEARNING_CLASSIFIER_MODEL` (default `KB_DRAFT_MODEL`).

### 9.6 Tests

Unit tests (offline, no model): classifier fallback and injection rules, trust factors, agreement
counting, evidence matching (quoted span must contain the claim's numbers), drafting refusals,
rollback trigger. Database tests (`KB_TEST_DATABASE_URL`, like the existing ones) with a stub
Module B: feedback → lesson → gate → live → the next `search` / headless answer cites the page;
feedback → rejected; plus the six adversarial tests in the brief. Then the full A, B and C suites
and the web unit tests, before and after.

## 10. Baseline test counts

First run (18 Nov 2026, no code changed) **without the local PostgreSQL server running**, so the
database tests could not connect. These numbers are not a valid baseline:

| Suite | Result |
|---|---|
| Module A (`knowledge`) | stopped at collection: `test_pgvector_db.py` could not connect |
| Module B (`assistant`) | 342 passed, 1 failed, 171 errors (database tests) |
| Module C (`experience`) | 24 passed, 7 failed, 92 errors (database tests) |
| Web (`vitest`) | 101 passed (29 files) |

Real baseline, with the database up, before the first code change: Module A 160 passed, 5
skipped; Module B 512 passed, 2 failed (voice-worker timing tests); Module C 123 passed; web 101
passed. After the change: see `REPORT.md`.
