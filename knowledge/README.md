# Module A — Knowledge & Learning

**Owner:** @adityadk03 · **Database schemas:** `kb`, `learn` · **PRD:** sections 8, 9.4, 17, 21.2, Appendix C

**Mission:** make the assistant know the right, current facts for each college, and learn from its mistakes under human control.

## Scope (Level 1)

- **Knowledge base (F4.1–F4.8):** upload and OCR, website crawl, program catalogue import, curated Q&A backend, metadata, approval and expiry, chunking and embeddings, hybrid search with reranking, versioning and rollback.
- **Currency rules (8.3, 17):** academic year and expiry on curated answers, catalogue-wins conflict check, 90-day unused flag, ownership registry, weekly KB health report, academic-year cut-over automation.
- **Tenant Pack backend (F10.1–F10.3, F10.6):** import, validation, readiness checklist, launch test set, content readiness review.
- **Agents:** Knowledge Ingestion Agent; Feedback Analyst Agent (nightly clustering, root cause incl. "correct but unwelcome", impact score, proposal drafting, approval SLA and escalation); Evaluation Agent (test-set management, DeepSeek-distill grading, regression gate, model-comparison harness with Module B).
- **Also:** data for the weekly gap report (F8.5); nightly export of top curated answers for degraded mode.

## Contracts

- **Provides:** `/kb/search`, `/catalogue/*`, `/curated-qa`, `/kb/sources`, `/kb/documents`, `/tenant-pack`, `/improvements`, `/evals/runs` — see `contracts/knowledge.openapi.yaml`.
- **Consumes:** Module B's chat API (to run tests headless); Module C's `feedback.created` and `correction.submitted` events.
- **By week 2:** a mock `/kb/search` over a fixture corpus so Module B can build against it.

## Thesis angle

Dense vs hybrid vs reranked retrieval on real admission questions; measured accuracy gain from correction cycles.

## Local prototype: running Module A

```bash
source local.env.sh                      # from the repository root
cd knowledge
python -m knowledge_api.migrate          # schemas kb + learn, RLS role admitai_kb
python -m knowledge_api.pack load ../packs/demo-college --tenant-id <uuid>
uvicorn knowledge_api.main:app --host 127.0.0.1 --port 8100
python -m pytest -q                      # offline: hash embedder, throw-away database
python -m knowledge_api.retrieval_eval ../packs/ruas --tenant-id <uuid>   # dense vs keyword vs hybrid
```

Every call carries `X-Tenant-Id: <uuid>`; Postgres RLS limits each transaction to that tenant.

| Endpoint | Purpose (PRD) |
|---|---|
| `POST /kb/search` | Hybrid search (vector + full-text, reciprocal-rank fusion) over approved, in-date chunks and curated answers, with academic-year and department filters (9.4, F4.6) |
| `GET /catalogue/programs[?facts=true]`, `GET /catalogue/programs/{id, code or alias}/facts` | Exact fees, dates, intake, duration and eligibility, never reformatted (F4.3, 21.4) |
| `POST /catalogue/import` | Catalogue CSV templates; every problem is reported at once |
| `POST /kb/documents`, `GET /kb/documents[/{id}]`, `PATCH /kb/documents/{id}` | Upload (PDF, DOCX, PPTX, XLSX, CSV, HTML, TXT, MD), approve / expire / reject / rollback, new versions with a diff summary (F4.1, F4.5, F4.8) |
| `POST /kb/sources`, `POST /kb/sources/{id}/sync` | Website crawl: robots.txt, include/exclude patterns, depth limit, change detection (F4.2) |
| `GET/POST /curated-qa`, `PATCH /curated-qa/{id}`, `POST /curated-qa/{id}/approve\|suspend\|expire\|rollback` | Curated answers with mandatory academic year and expiry, version history (F4.4, 8.3) |
| `POST /events`, `GET /events` | `feedback.created` / `correction.submitted` from Module C, de-duplicated |

**Vector index (PRD 9.4, F4.7).** Where the PostgreSQL server has the pgvector extension (the
lab's server), `python -m knowledge_api.migrate` adds `vector(768)` / `vector(1024)` columns
generated from the stored embeddings and HNSW indexes, and search ranks by them in the database
(`KB_VECTOR_BACKEND=auto`, the default). Without the extension (laptops, GitHub CI) the same
vectors are searched in the API. Both give the same results (`tests/test_pgvector_db.py`).
`KB_PGVECTOR=off` skips the columns; `KB_VECTOR_BACKEND=memory` forces the in-API search.

**OCR (F4.1).** A PDF with almost no text is a scan: its page images go to Tesseract
(`tesseract` on the PATH, or `KB_TESSERACT`; languages `KB_OCR_LANGS`, default `eng`). The
document stays a draft with the note "Text read by OCR … check numbers and dates" until staff
approve it. Without Tesseract a scan is kept `needs_review`, as before. Tesseract must be in
Module A's container image on the lab (the guide's `deploy/`).

**Reranker (PRD 9.4).** `bge-reranker-base` (MIT) as int8 ONNX, run with onnxruntime on the CPU
(no PyTorch). It reorders the best 8 hybrid results, seeing each passage with its document title
and section; Indic-script questions keep their hybrid order (the model is English-only).
Speed (17 Nov 2026): reranking was ~95% of a search on a laptop CPU. It now reads 256 tokens of
each passage (`KB_RERANK_MAX_TOKENS`, was 512): a search went from 3.2 s to 1.4 s with the same
or better hit@1/hit@4 on RUAS's test questions, and it remembers (question, passage) scores, so a
repeated question takes 0.05 s (never stale: a changed passage is scored again). Download
once (~280 MB), and `local.env.sh` turns it on when it is there:

```bash
python -c "from huggingface_hub import hf_hub_download as d; [d('Xenova/bge-reranker-base', f, local_dir='.local/models/reranker/bge-reranker-base') for f in ('onnx/model_int8.onnx','tokenizer.json','tokenizer_config.json','config.json')]"
python -m knowledge_api.retrieval_eval ../packs/ruas --tenant-id <uuid> --modes hybrid,rerank
```

Measured locally: about 0.1–0.25 s per search on a laptop CPU; right source first 66 → 68 of 89
answerable questions; answer accuracy unchanged for the demo college (100%) and Riverside (87%),
RUAS 76–80% → 81%, supported claims on RUAS 69–80% → 84%. Without context (title and section) it
made things worse ("Graduates work in automotive …" ranked first for a question about CSE jobs).

Retrieval on the RUAS set (72 questions, hybrid, top 4): English direct 5/5, paraphrase 19/20, Hinglish 3/3. Questions in Indic scripts are searched with a second index (bge-m3, 1024-d): Hindi 1/10 -> 10/10 and Kannada 3/16 -> 15/16 compared with nomic-embed-text alone, which is English-only. Drafts, the internal FAQ and expired documents were never cited. Vector scores do not separate answerable from must-refuse questions ("Does RUAS offer scholarships?" scores 0.78), so the no-answer decision is made in Module B's answer step.

## Completing Module A for Level 1 (November 2026)

What was added on top of the prototype, with the requirement each one closes:

| Requirement | What | Where |
|---|---|---|
| F4.1 OCR | Scanned PDFs are read by Tesseract and kept as drafts with a warning | `ocr.py`, `ingest.py` |
| F4.7 pgvector HNSW | Vector search in the database where pgvector exists; same results as in memory | `migrate.py`, `search.py` |
| F2.10 voice consistency | Test set answered as chat and as voice; both scores sent to Module B (`POST /voice/eval-result`) | `voice_eval.py`, `POST /evals/voice` |
| F8.3 implicit signals | `signal.logged` events (rephrase, talk to a human, abandonment, voice hang-up, barge-in) | `routes/learn.py`, `learning.py` |
| 8.3 approval SLA | Overdue proposals go to a named delegate first, then the College Admin | `learning.refresh_pending`, `PUT /learning/settings` `delegates` |
| 8.3 approval quality | Each month 10% of last month's approved proposals get a second approver (not the first) | `quality.py`, `/improvements/{id}/second-review` |
| F7.7 weekly digest | The top-10 e-mail as data, for Module C to send; no visitor text in it | `GET /reports/digest` |
| F9.3 quality dashboard | Acceptance rate, eval score per run, proposals opened/approved/deployed, per week | `GET /reports/quality` |
| F10.3 catalogue cases | Launch test cases generated from the catalogue (exact fees, intake, dates); not counted as real questions | `catalogue_cases.py` |
| 20 judge grading | Rubric grading by DeepSeek-R1-Distill-Qwen-14B, cross-checked by the Llama-8B distill; disagreements and a random 10% to a person; only in the scheduled window | `judge.py`, `/evals/runs/{id}/judge`, `/human-grade` |
| 20 model comparison | The test set answered by several models on sandbox copies; accuracy, groundedness, latency, memory | `comparison.py`, `/evals/comparisons` |

New settings (all optional): `KB_JUDGE_URL` (default `LLM_BASE_URL`), `KB_JUDGE_MODEL`,
`KB_JUDGE_CHECK_MODEL`, `KB_JUDGE_WINDOW` (default `22:00-06:00`), `KB_JUDGE_TIMEZONE`
(default `Asia/Kolkata`), `KB_MODEL_STATS_URL` (Ollama, for GPU memory in comparisons),
`KB_TESSERACT`, `KB_OCR_LANGS`, `KB_OCR_MAX_PAGES`, `KB_PGVECTOR`, `KB_VECTOR_BACKEND`.

```bash
python -m knowledge_api.voice_eval --tenant-id <uuid>                                 # F2.10
python -m knowledge_api.comparison --tenant-id <uuid> --models local/qwen2.5-14b-instruct,local/llama3.1-8b
```

Not measured yet: judge grading and model comparison with the real DeepSeek distills and the
14B model (they need the lab GPUs; the tests use stand-ins); OCR quality on real scans; HNSW
speed at lab scale. Module C still has to send `signal.logged` events and e-mail the digest;
the changes are proposed in `MODULE-C-CHANGES.md`.

## The learning loop (PRD 8)

```bash
python -m knowledge_api.learning run --tenant-id <uuid>   # nightly Feedback Analyst (or POST /improvements/analyse)
python ../scripts/e2e_learning.py --rollback              # live: correction -> proposal -> gate -> deployed -> rollback
```

| Endpoint | Purpose (PRD) |
|---|---|
| `GET /improvements[?status=]`, `GET /improvements/{id}` | Proposals ranked by impact, with examples, diff and gate result (8.3); Module C's approval inbox |
| `POST /improvements/{id}/approve\|reject\|rollback` | Approve runs the evaluation gate in the background, then deploys; rollback undoes a deployed change (8.4) |
| `POST /improvements/analyse` | Run the Feedback Analyst now |
| `POST /evals/runs`, `GET /evals/runs[/{id}]` | Run the tenant's test set through Module B; results per case (20) |
| `GET /reports/weekly` | Acceptance rate, knowledge gaps, fixed this week, approval backlog and its age, top-10 digest, KB health, feedback anomalies, proposals held for peak season (8.4, 8.6, 17.2, F8.5) |
| `GET/PUT /learning/settings` | Peak-season mode: only wrong-fact proposals reach experts; the rest are held (not escalated) until it is switched off (8.3). `GET /improvements` then reports `held`; `include_held=true` lists them |
| `POST /cutover` | Academic-year cut-over: old-year documents expire, curated answers re-issued as drafts (17.3) |
| `GET /export/degraded` | Top approved answers for the degraded-mode page (10.2) |

1. **Signals** (8.1): thumbs and ratings from Module C (question, answer and citations fetched from Module B by message id), staff corrections, and Module B's no-answer / blocked / partial turns.
2. **Triage, correctness first** (8.2): an answer that passed Module B's sentence checks and was marked down without a reason or as "not what I asked" is *correct but unwelcome*: no change, excluded from the acceptance rate. Otherwise: outdated/wrong source, knowledge gap, wrong answer corrected by staff, tone, or a sensitive topic.
3. **Proposals** (8.3): similar questions are clustered (bge-m3 vectors, cosine >= 0.80; reports about one document are grouped by that document). Impact = conversations in the last 7 days x severity (3 wrong fact, 2 missing knowledge, 1 tone) x recency. Due in 3 working days; overdue proposals escalate to the College Admin. The cheapest rung is proposed: curated answer, expire a document, add a sensitive topic (in Module B), or a prompt change (manual). Catalogue values are corrected by re-importing the catalogue.
4. **Gate** (8.4): the tenant's knowledge is copied to a sandbox tenant, the test set is answered through Module B before and after the change, and the change reaches the real tenant only if no metric falls by more than 1 point, no previously passing critical case fails and the proposal's own new cases pass. A case that newly fails is asked once more (a 3B model still varies slightly at temperature 0). Passing proposals add their cases to the regression set.
5. **Abuse resistance** (8.4): of the negative ratings of one question, at most 5 per hour count (`feedback_burst_limit`); the rest stay on record as `suspected_abuse`, never reach a proposal, and appear under `feedback_anomalies` in the weekly report. Feedback alone never changes an answer: every change needs an approval and the gate.
6. **Currency rules**: catalogue wins (a curated answer quoting an amount the catalogue does not have for that program is suspended at once, on every import and nightly); answers unused for 90 days and documents near expiry appear in the weekly report.

Live run on the demo college (25 cases, qwen2.5:3b): baseline accuracy 89.5% -> candidate 90.0%, deployed in about two minutes; the next visitor got the officer's answer with its citation; rollback restored the previous behaviour. Grading is deterministic (expected citations and facts, forbidden citations, refusals, routing); the PRD's DeepSeek-distill judge needs the lab GPUs.

## Self-learning knowledge layer (November 2026, off by default)

With `KNOWLEDGE_SELF_LEARNING_ENABLED=true` (and `LEARNING_SESSION_SALT`), a worker turns each
new feedback into evidence-checked lessons within seconds. The lessons are aliases, retrieval
boosts, curated answers copied from the college's own pages, and catalogue re-extraction (always
staff-approved). Each lesson goes through the evaluation gate (plus a stratified sample, zero
tolerance and a Hit@1/Hit@4 check) before it is live. Live lessons whose ratings fall are rolled
back automatically. Lessons appear in the Approvals inbox as proposals. With the flag off,
nothing changes. Design, rules, configuration, demo and limitations:
[`docs/self-learning/README.md`](docs/self-learning/README.md); migration `010_self_learning.sql`;
report `GET /learning/self/report`.

## Sharing the model with visitors, and drafting answers (November 2026)

- **One test suite at a time, visitors first.** `gate.run_suite` takes a database advisory lock, so
  launch tests, evaluation runs and gates use the model one at a time across all processes (waiting
  up to `KB_SUITE_QUEUE_MAX_S`, default 1 hour). Before each question it asks Module B's `GET /load`
  how many visitor answers are being written, and waits while any are, up to `KB_GATE_YIELD_MAX_S`
  (default 30 s) per question. If Module B cannot say, it carries on. Each run's scores record
  `waited_for_visitors_s`.
- **Draft from documents.** `POST /improvements/{id}/draft` drafts the missing answer of a pending
  "No reliable answer" proposal from the college's approved documents, with the answer model
  (`KB_DRAFT_MODEL`). It runs only on request, and nothing goes live from it. It returns no draft,
  with the reason, when no document scores at least `KB_DRAFT_MIN_SCORE` (0.50), when the model
  cites nothing or finds no answer, or when the draft has a number that is not in the cited
  documents. The draft is stored on the proposal as `diff.draft`.
- **Reusing a baseline.** Each gate run stores a fingerprint of what it measured: the knowledge
  base, catalogue and test set (row hashes), Module B's settings and its code, agent and prompt
  versions (`GET /health`), and the date. A later gate for the same unchanged college reuses that
  run as its baseline instead of answering every case again; the proposal's new cases still run.
  A reused baseline is never older than 24 hours, and when Module B cannot report its versions,
  nothing is reused. The gate records `baseline_reused_from`. Migration `008_baseline_reuse.sql`.
- **`GET /improvements/corrections?ids=...`** says where each staff correction is (received,
  waiting for approval, testing, live, failed, rejected, rolled back, not used), by following its
  `correction.submitted` event to the signal and the proposal. Module C's review page shows it.
- **A staff correction becomes a proposal at once.** `POST /events` with `correction.submitted`
  reads new feedback and makes proposals in the background (only those two steps), so the
  correction is in the approval inbox within seconds instead of after the nightly run. Weak
  signals (thumbs-down, low ratings) still wait to be grouped nightly. If it fails, the nightly
  run picks the correction up.

## Knowledge that builds itself from the college's website (`site_kb.py`)

**Website → crawl → knowledge base → catalogue and FAQs → assistant (chat, Retell voice) and the
college's pages.** A Tenant Pack names the site (`website:` in `pack.yaml`: start URL, include
patterns, page limit); nothing about one college is written into the code.

1. Pages come from the sitemap named in robots.txt (robots.txt is obeyed), plus one round of links
   the sitemap missed (renamed pages). Stale sitemap entries (404) are counted and skipped.
2. Menus and footers are learned from the crawl itself: block runs that open or close most pages
   are dropped, so only each page's own content is indexed (about half of every page).
3. Every page becomes an approved document (both embedding models). A changed page becomes a new
   version; an unchanged one is skipped, so a nightly re-run re-indexes only what changed.
4. Programme pages (a title plus sections such as "Eligibility" or "Fee Structure") give the
   catalogue: name, level, faculty, duration, intake, eligibility, fees and the page's address
   (`kb.programs.source_url`, migration 009). Values are copied from the page, never generated. A
   fee table with one column per quota gives one fee per quota; a page that still shows last
   year's fee table keeps the programme and labels the fee "(2025-26 fee structure)". Aliases
   come from the name (without dots, "in", "(Hons)"), from acronyms the page spells out
   ("Master of Dental Surgery(MDS)" → "MDS Periodontics"), and from the previous entry with the
   same name (staff-added aliases and codes keep working). The catalogue is replaced only if the
   crawl found at least half as many programmes as there are today.
5. FAQ headings ("…?" with the answer below) become approved curated answers; one matching an
   existing curated question updates it as a new version, so references to it stay valid.
6. The catalogue test cases are regenerated, so the evaluation gate tests the new catalogue.
7. **Tables are indexed row by row** (`ingest.table_rows`, also for uploads and `/kb/sources`):
   each value stays next to its column heading ("Course Title: Linear Algebra; Total Credits: 3"),
   so a chunk never holds a run of loose numbers. Serial-number columns are left out.
8. **Curriculum summaries** (`curriculum.py`). A programme page whose curriculum is a table per
   semester or year, with a credits column, gets one more approved document at
   `<page>#curriculum-summary`: total credits, credits per semester, and credits per subject area
   with the share of the programme ("Mathematics and statistics (maths) courses carry 46 of the
   164 credits, which is 28.0% of the programme"), listing the courses counted. Questions such as
   "what % of the maths and computing course is maths?" are answered from it; no single table
   row could answer them. Subject areas come from course titles; electives and projects are
   counted on their own because their subject varies. A semester offered as alternatives
   ("Physics Cycle" / "Chemistry Cycle") is counted once, the alternatives in turn when the next
   semester offers the same ones, and the summary says which tables it did not count.
9. **Linked files.** Same-site PDF, DOCX, PPTX and XLSX files that the read pages link to
   (regulations, fee notices, brochures) are indexed too, up to `website.documents.max` (150)
   files of at most `website.documents.max_mb` (40) MB; include patterns choose pages, not files.
   A text file is approved like a page. A scanned PDF read by OCR stays a draft with its warning
   until staff approve it; without Tesseract it stays `needs_review`. A file already indexed is
   downloaded again at most once a week.

Run: `python -m knowledge_api.site_kb --tenant-id <uuid> --pack ../packs/<college>/pack.yaml`;
`scripts/nightly_local.sh` runs it for every college whose pack has `website:`. RUAS (13 Nov 2026):
104 pages, 81 programmes (eligibility for 80, fees for 72), 188 FAQs, about 6 minutes on a laptop;
an unchanged re-run takes about a minute. Module C serves the catalogue to the college's own pages
at `GET /widget/programmes?public_key=…` (the demo site no longer has a hand-copied data file).
