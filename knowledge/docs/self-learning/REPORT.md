# Self-learning knowledge layer: report

18 Nov 2026. Two branches, neither pushed nor merged:

| Branch | Base | What |
|---|---|---|
| `a/module-a-self-learning` | `origin/main` `3a01229` | The self-learning layer, all in `knowledge/` (Module A) |
| `c/feedback-session-and-comment` | `origin/main` `3a01229` | Two small Module C changes the layer benefits from (the owner agreed to Module C changes) |

Module B, `contracts/`, `deploy/` and `.github/` are untouched: `git diff --stat origin/main --
assistant contracts deploy .github` is empty on both branches. The Module A branch does not
touch `experience/` either. The Module A branch is named `a/…`, not `feature/…`, as the root
`CLAUDE.md` requires.

## What was built

### Module A (`knowledge/`)

| Part | Files |
|---|---|
| Migration (schema `learn`, RLS `tenant_isolation`) | `db/migrations/010_self_learning.sql`: `feedback_events`, `feedback_analysis`, `trust_scores`, `lessons`, `lesson_evidence`, `lesson_metrics`, `lesson_aliases`, `retrieval_boosts`, `knowledge_versions`; new proposal kinds; a `NOTIFY` trigger on `learn.events` |
| Pipeline | `knowledge_api/selflearn/`: `classify.py`, `trust.py`, `agree.py`, `evidence.py`, `draft.py`, `apply.py`, `gatecheck.py`, `live.py`, `pipeline.py`, `monitor.py`, `worker.py`, `report.py` |
| Hooks into existing code | `search.py` (aliases and boosts, only with the flag on); `gate.py` (lesson kinds in apply/rollback/new cases; stratified sample, zero tolerance and Hit@1/Hit@4 for lesson proposals; sandboxes copy live lessons); `main.py` (worker started only with the flag and a salt); `learning.py` (erasure covers the new tables); `routes/kb.py` (passes the flag) |
| Endpoint | `GET /learning/self/report` (read-only), also `python -m knowledge_api.selflearn.report` |
| Settings | `config.py`, `knowledge/.env.example`: `KNOWLEDGE_SELF_LEARNING_ENABLED` (default `false`), `LEARNING_SESSION_SALT`, `LEARNING_MIN_AGREEING_SESSIONS` (3), `LEARNING_MIN_TRUST`, `LEARNING_AUTO_LIVE` and others (see README) |
| Tests | `tests/test_selflearn_unit.py` (13), `tests/test_selflearn_db.py` (10) |
| Demo | `scripts/demo_self_learning.py` |
| Docs | `docs/self-learning/DISCOVERY.md`, `README.md`, this report; a section in `knowledge/README.md` |

Existing pieces reused, not duplicated:
- the evaluation gate, sandbox copies, rollback and baseline reuse (`gate.py`);
- the approval inbox, through `learn.proposals`;
- the crawler's page parser and programme extractor (`ingest.parse_html`, `site_kb.programme`);
- hybrid search and the shared paraphrase vocabulary;
- fee changes with rollback (`gate._change_fees`);
- the PII masker, the answer-model client (`drafting.GatewayDraftModel`) and the event intake
  (`POST /events`).

### Module C (`experience/`, separate branch)

- `feedback.created` events carry `session_fp`, a SHA-256 hash of the visitor's session (never
  the id). Module A salts it again. This gives the 3-distinct-sessions rule real sessions instead
  of conversations.
- After "Wrong", "Outdated" or "Incomplete", the widget offers an optional "Tell us more" box with
  Skip; "Other" still requires its text. The step logic is in `web/src/lib/feedback.ts`, with
  tests. The new "Skip" label is in English, Hindi and Kannada.

The two branches are independent. Without the Module C branch, Module A falls back to the
conversation id.

## Test results

| Suite | Before | After |
|---|---|---|
| Module A (`knowledge`) | 160 passed, 5 skipped | 183 passed, 5 skipped (23 new) |
| Module B (`assistant`) | 512 passed, 2 failed | 514 passed |
| Module C (`experience`) | 123 passed | 124 passed (1 new, on the C branch) |
| Web (`vitest`) | 101 passed (29 files) | 107 passed (30 files) |
| Contracts check | – | 124 passed (1 new, on the C branch)ONTRACTS |

Module B's 2 failures in the baseline were both voice-worker timing tests
(`test_audio_starts_after_the_first_sentence_not_the_whole_answer`,
`test_barge_in_stops_the_audio_within_300_ms_and_drops_the_rest_of_the_answer`). They passed in
the second run with Module B's code unchanged: they measure milliseconds on a laptop and are
timing-sensitive, not broken. Lint (`ruff check`, `ruff format --check`) is clean for Modules A
and C, and `tsc`, `eslint` and `vitest` pass for the web app. The contract check rewrites
`docs/proposed-contracts/*.json` as a side effect; those files were put back, because they are
outside Module A's folder and were already out of date. No Module B or Module C test was changed; one Module C test was added.

New tests and what they show:

| Test | Shows |
|---|---|
| `test_three_sessions_teach_a_lesson_that_the_next_session_uses` | Feedback → lesson → gate → live; the next session cites the right page; staff rollback from the inbox works and search forgets the lesson |
| `test_no_evidence_means_no_lesson` | Feedback → rejected when the college's pages do not answer it |
| `test_a_fake_fee_claim_never_changes_the_catalogue` | "fees are ₹10" is rejected against the page; with no page it goes to staff, and approving changes nothing |
| `test_a_supported_fee_claim_uses_the_page_value_and_waits_for_approval` | A true claim becomes a re-extraction with the page's value, pending approval |
| `test_fifty_complaints_from_one_session_do_not_meet_the_threshold` | One session counts once; the flood is not trusted |
| `test_prompt_injection_in_the_comment_box_is_ignored` | Classified `junk_abuse`, trust 0, no lesson; the model is never called |
| `test_a_lesson_that_breaks_another_question_is_blocked_by_the_gate` | `failed_gate`, the broken case is named, and nothing changes for visitors |
| `test_a_live_lesson_whose_ratings_drop_is_rolled_back` | Automatic rollback, recorded with the reason |
| `test_with_the_flag_off_nothing_changes` | Identical search results even with lesson rows present; no new rows; no worker |
| `test_the_worker_starts_only_with_the_flag_and_a_salt` | The worker starts only with both |
| Unit tests | Classifier rules and model-reply validation, BC category not treated as abuse, trust factors, salted fingerprints, agreement counting, verbatim quotes, money parsing, alias versus boost, protected lessons never auto, rollback trigger |

## Demo output

`python scripts/demo_self_learning.py` (abridged):

```
=== 1. A question that fails today
What scholarship do I get for 90 percent marks?
cited: ['Admissions brochure'] -> Scholarship marks percent: scholarship marks percent details for 90 percent marks are prin

=== 2. Three visitors in different sessions rate it down
  session-a: thumbs-down, 'wrong page, not what I asked'
  session-b: thumbs-down, 'wrong page, not what I asked'
  session-c: thumbs-down, 'wrong page, not what I asked'

=== 3. The worker: classify -> trust -> agreement -> evidence -> gate -> live
  feedback 8: retrieval_miss, trust 0.8, waiting for agreement
  feedback 9: retrieval_miss, trust 0.8, waiting for agreement
  feedback 10: retrieval_miss, trust 0.8, gated
  lesson: live, knowledge version 1
  gate: passed=True retrieval {'hit@1': 0.6667, 'hit@4': 1.0, 'cases': 3} -> {'hit@1': 1.0, 'hit@4': 1.0, 'cases': 3}

=== 4. A new session asks the same question
cited: ['Merit scholarship policy'] -> Merit scholarship: 90 percent marks get a 50 percent tuition fee waiver.

=== 5. A fake fact claim: 'fees are ₹10 for B.Tech CSE'
  lesson rejected: the source contradicts the claim: the page lists 250000, as the catalogue does; not 10
  catalogue fee unchanged: 250000.00

=== 6. Lesson log
  [rejected] fact: What is the fee for B.Tech CSE? | sessions 1 | the source contradicts the claim: the page lists 250000, as the catalogue does; not 10
  [live] retrieval_boost: What scholarship do I get for 90 percent marks? | sessions 3 | passed the evaluation gate
  knowledge version 1: apply Learnt: find 'Merit scholarship policy' for 'What scholarship do I get
```

## Limitations

- **Distinct sessions:** without the Module C branch, a "session" is a conversation. A visitor
  who reloads the widget looks like a new one; the trust score only partly covers this.
- **Retrieved chunk ids:** Module B keeps only cited sources. `retrieved_refs` is Module A's own
  search for the question, labelled as such (gap B1).
- **Automatic fact checks** cover fees and intake on programme pages. Dates, credits and marks go
  to staff.
- **Evidence without a model** is picked by word overlap. With `LEARNING_CLASSIFIER=llm`, the
  answer model picks the passage and must quote it exactly. The gate is the safety net either
  way. The model path is tested with stand-ins only; it has not been run against the lab model.
- **Boosts cannot get past Module B's no-answer floor.** A passage Module B would refuse stays
  refused, and the gate reports that the lesson's own question is not found.
- **Timing:** feedback is classified within seconds. A lesson goes live only after the gate, which
  takes minutes on the shared model.
- **Not run against the live stack:** the demo uses the offline embedder and a stand-in for
  Module B, as the gate's own tests do. A run with the real Module B and Ollama is still to do.

## Needs Module B or C, not done

| # | Module | Change | Status |
|---|---|---|---|
| C1 | C | Session hash on feedback events | Done, on `c/feedback-session-and-comment` |
| C2 | C | Comment box after Wrong / Outdated / Incomplete | Done, same branch |
| C3 | C | Feedback after phone calls (Retell, Vapi) and their channel values | Not done (needs a post-call feedback flow) |
| B1 | B | Record retrieved (not only cited) chunk ids | Not done |
| B2 | B | Read answer hints from Module A and use them in the prompt | Not done; until then, style lessons go to the inbox as `prompt` proposals |

## Turning it on, and rolling back

```bash
python -m knowledge_api.migrate                       # applies 010_self_learning.sql
export KNOWLEDGE_SELF_LEARNING_ENABLED=true
export LEARNING_SESSION_SALT=$(python -c "import secrets; print(secrets.token_hex(32))")
# restart Module A: the worker starts with it
```

- **One lesson:** "Roll back" on the Approvals page, or `POST /improvements/{proposal_id}/rollback`.
- **Everything:** set `KNOWLEDGE_SELF_LEARNING_ENABLED=false` and restart. Search ignores every
  lesson and the worker does not start.
- **Live catalogue changes:** these were approved by staff. Roll them back one by one, as above.
- **The migration:** it only adds tables and widens two constraints, so it can stay in place with
  the flag off.
