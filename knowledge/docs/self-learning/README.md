# Self-learning knowledge layer (Module A)

Module A learns from every conversation's feedback, automatically and safely, so that a problem
reported in one session is fixed for the next. Learning happens only in the knowledge layer:
aliases, retrieval boosts, curated answers copied from the college's own pages and catalogue
re-extraction. No model is trained or changed. Everything is per college, versioned and reversible.

**Off by default:** `KNOWLEDGE_SELF_LEARNING_ENABLED=false`. With it off, nothing below runs, no
new rows are written and search ignores lessons; the existing suite runs unchanged.

## The rules, and where each is enforced

| Rule | How |
|---|---|
| Feedback is a signal, never the truth | A lesson needs a `learn.lesson_evidence` row: an exact quote from the college's own indexed page or document (`evidence.is_verbatim`), or a re-fetched programme page. The visitor's comment is never copied into knowledge |
| Numbers and dates are protected | A fact claim only triggers a re-fetch and re-extraction of the programme page (`site_kb.programme`). A new value is the page's, never the visitor's, and a catalogue change always waits for staff approval (`catalogue_reextract` is never in `LEARNING_AUTO_LIVE`'s reach) |
| No fine-tuning | Lessons are rows in `learn`; nothing calls a training endpoint |
| Per college | Every new table has `tenant_id` and the `tenant_isolation` RLS policy (`kb.current_tenant()`), like the rest of `learn` |
| Agreement | Retrieval and style lessons need `LEARNING_MIN_AGREEING_SESSIONS` (3) distinct, trusted sessions; fifty complaints from one session count once |
| Gate | The existing evaluation gate, plus a stratified sample of the whole test set, zero tolerance and a retrieval Hit@1/Hit@4 check (`selflearn/gatecheck.py`) |
| Self-heal | Live lessons whose ratings fall, or whose cases fail the nightly full test, are rolled back with the existing rollback (`selflearn/monitor.py`) |

## Pipeline

```
Module C ──feedback.created──▶ POST /events ──▶ learn.events ──NOTIFY──▶ worker
                                                                          │
 1 understand  classify.py   rules first (injection, abuse), then the model (validated)
 2 trust       trust.py      rate, duplicates, consistency with the answer, history
 4 agreement   agree.py      similar questions from ≥ 3 distinct trusted sessions
 3 evidence    evidence.py   indexed passage (exact quote) · re-fetched programme page
 5 draft       draft.py      alias · retrieval boost · curated answer from the page · re-extraction
 6 gate        gate.py + gatecheck.py   sandbox copy, before/after, zero regressions
 7 live        apply.py      new knowledge version; search uses it on the next question
 8 monitor     monitor.py    live ratings and nightly test → automatic rollback
```

Each feedback is processed once: the worker claims rows with `for update skip locked`, retries a
failure up to three times with back-off, and marks it `failed` after that. It is woken by
PostgreSQL `NOTIFY` (a trigger on `learn.events`) and also polls every `LEARNING_POLL_S` seconds.
Classification takes seconds; the gate takes minutes, because it answers test questions through
Module B on the shared model, one suite at a time, yielding to visitors.

### Classes and what each can become

| Class | Needs | Lesson | Live without a person? |
|---|---|---|---|
| `retrieval_miss`, `missing_answer` | 3 sessions + a passage on the college's own page that answers it, not the page the disliked answers cited | `alias` if the visitor's term is the only gap, else `retrieval_boost`; if the boost fails the gate, one retry as `curated_from_source` | yes, after the gate |
| `wrong_fact_claim` | the programme page, fetched again | page supports the claim → `catalogue_reextract` with the page's value; contradicts → rejected; cannot tell → staff inbox | **never** |
| `answer_style` | 3 sessions | `answer_hint`, sent to the inbox as the existing `prompt` proposal | never (Module B has no input for hints) |
| `praise` | – | recorded; counts towards live monitoring | – |
| `junk_abuse` | – | stored, logged, trust 0 | – |

A boost puts the evidence passage first for questions whose vector is within
`LEARNING_BOOST_SIMILARITY` of the lesson's question cluster. Its vector score is reported as it
is, so Module B's own no-answer floor still decides whether the passage may be used. A boost
cannot rescue a passage Module B would refuse; the gate shows that as "the lesson's own question
is still not found".

### Trust

A score from 0 to 1 (staff 1.0, visitors start at 0.8). It is reduced by:
- injection or abuse (to 0);
- a link in the comment;
- a flood (≥ `LEARNING_MAX_FEEDBACK_PER_HOUR` from one session);
- duplicates;
- rating the same answer both up and down;
- a history of bad feedback from that session;
- quoting a figure the answer never said ("you said 5 lakh");
- a conversation that cannot be found.

Below `LEARNING_MIN_TRUST` (0.6), feedback is stored but teaches nothing. The session
fingerprint is a salted SHA-256 hash of Module C's session hash when it sends one, otherwise of
the conversation id.

## Staff approval inbox

Every lesson is a row in `learn.proposals`, so it appears in Module C's Approvals page with its
examples, evidence and reason, and the existing approve, reject and rollback buttons work:
- Lessons that went live by themselves show as `deployed`, decided by `self-learning`.
- Unverifiable claims, page changes and protected values arrive as `pending`. Approving them runs
  the gate, like any other proposal.
- The worker copies each decision back to the lesson, recording a knowledge version when a lesson
  goes live or is rolled back.

## Data

Migration `db/migrations/010_self_learning.sql` adds these tables to the `learn` schema:

| Table | Holds |
|---|---|
| `feedback_events` | One row per feedback, with the masked comment, question and answer, citations, Module A's own search results and the session fingerprint |
| `feedback_analysis` | The class, claim, model and reasoning |
| `trust_scores` | Per feedback and per session |
| `lessons` | Each lesson and its status |
| `lesson_evidence` | Quote, URL, document, content hash and fetch time |
| `lesson_metrics` | Gate results, live signals and nightly results |
| `lesson_aliases`, `retrieval_boosts` | What search reads |
| `knowledge_versions` | One row per lesson going live or being rolled back |

The migration also widens the allowed proposal kinds to include `retrieval_boost`,
`search_alias` and `catalogue_reextract`. Personal data is masked with `pii.mask_pii` before it is
stored. An erasure request also clears these tables.

## Configuration

See `knowledge/.env.example`. When the flag is on, `LEARNING_SESSION_SALT` is required: the
worker does not start without it, and a production start-up refuses to run.

| Variable | Default | Meaning |
|---|---|---|
| `KNOWLEDGE_SELF_LEARNING_ENABLED` | `false` | The switch |
| `LEARNING_SESSION_SALT` | – | Secret salt for session fingerprints |
| `LEARNING_MIN_AGREEING_SESSIONS` | 3 | Distinct trusted sessions for retrieval and style lessons, and for a rollback |
| `LEARNING_MIN_TRUST` | 0.6 | Feedback below this teaches nothing |
| `LEARNING_AUTO_LIVE` | `alias,retrieval_boost,curated_from_source` | Lesson types that may go live after the gate without a person |
| `LEARNING_CLASSIFIER` | `llm` | `llm` (the answer model through the gateway, rules as fallback) or `rules` |
| `LEARNING_CLASSIFIER_MODEL` | `KB_DRAFT_MODEL` | Model for classification and evidence picking |
| `LEARNING_POLL_S` | 5 | Fallback poll interval |
| `LEARNING_MAX_FEEDBACK_PER_HOUR` | 10 | Per session, before it counts as a flood |
| `LEARNING_REFETCH_AFTER_H` | 24 | A cited page older than this is fetched again |
| `LEARNING_BOOST_SIMILARITY` | 0.85 | How close a question must be to a lesson's cluster |
| `LEARNING_ROLLBACK_MIN_VOTES` / `_DOWN_RATIO` | 5 / 0.6 | Live ratings that trigger a rollback |
| `LEARNING_GATE_SAMPLE` | 20 | Questions from the whole test set added to each lesson's gate |
| `LEARNING_BACKFILL_H` | 24 | Older feedback is not processed when the flag is first turned on |

## Turning it on, and off

**Local development:** it is on. `local.env.sh` sets `KNOWLEDGE_SELF_LEARNING_ENABLED=true`,
generates each developer's own `LEARNING_SESSION_SALT` into the git-ignored `local.secrets.sh`,
and `scripts/up_windows.sh` / `scripts/run_local.sh` apply Module A's migrations at start-up. To
try without it: `export KNOWLEDGE_SELF_LEARNING_ENABLED=false` before `source local.env.sh`.
Elsewhere (lab, production, CI) the code default is off; turn it on as below.

```bash
python -m knowledge_api.migrate                       # migration 010
export KNOWLEDGE_SELF_LEARNING_ENABLED=true
export LEARNING_SESSION_SALT=$(python -c "import secrets; print(secrets.token_hex(32))")
uvicorn knowledge_api.main:app --host 127.0.0.1 --port 8100   # starts the worker
```

- **One lesson:** roll it back from the Approvals page, or with
  `POST /improvements/{proposal_id}/rollback`. Search stops using it on the next question.
- **Everything:** set `KNOWLEDGE_SELF_LEARNING_ENABLED=false` and restart. Search then ignores all
  lessons and the worker does not start.
- **Live catalogue changes:** these were approved by staff. Roll them back one by one, as above;
  switching the flag off does not undo them.

## Observing it

- `GET /learning/self/report`, or `python -m knowledge_api.selflearn.report --tenant-id <uuid>`.
  This lists feedback by class and status, lessons by status, gate results, rollbacks, knowledge
  versions and the trust-score distribution.
- Logs: one `admitai.selflearn` line per stage, with tenant, feedback and lesson ids.
- `python -m knowledge_api.selflearn.worker once --tenant-id <uuid>` processes everything waiting
  for one college at once.

## Demo

```bash
source local.env.sh && cd knowledge && python scripts/demo_self_learning.py
```

The demo runs on a throw-away demo college in the local database, with the offline embedder and
a stand-in for Module B. It shows:
1. a retrieval miss;
2. three sessions rating the answer down;
3. the worker classifying, gating and making the lesson live;
4. the corrected, cited answer in a new session;
5. a fake fee claim rejected against the programme page;
6. the lesson log.

## Limitations

- **Distinct sessions:** without Module C's session hash, a "session" is a conversation, and a
  visitor who reloads the widget looks like a new one. The trust score lowers the risk but cannot
  remove it.
- **Retrieved chunks:** Module B records only cited sources. `retrieved_refs` is Module A's own
  search for the question, labelled as such.
- **Automatic fact checks:** only fees and intake are checked against a programme page
  automatically. Other protected values (dates, credits, marks) go to a person.
- **Evidence picking without a model** uses word overlap, which is weaker than the model's
  judgement. The gate is the safety net.
- **Answer hints** cannot change answers until Module B reads them.
