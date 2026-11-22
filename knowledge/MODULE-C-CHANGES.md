# What Module A's Level 1 completion needs from Module C and the guide

For Module C's owner and the guide. Module A's own work is complete and tested in `knowledge/`.
These items are outside Module A's folder, so they are proposals; each owner decides.

## Module C

### 1. Send implicit signals to Module A (PRD F8.3), ready as a patch

`module-c-learning-signals.patch` (sent with this pull request, not committed here) is a small,
tested change in `experience/`:

- `routes/signals.py`: when the widget logs `rephrase`, `talk_to_human`, `voice_hangup` or
  `barge_in` on `POST /conversation-events`, an outbox event `signal.logged` is queued, with
  `{kind, conversation_id, message_id, channel}`. No visitor text is sent.
- `db/migrations/009_learning_signals.sql`: allows `signal.logged` in `app.outbox_events`.
- One test. Module C's suite: 87 passed with it.

The existing outbox delivery sends it to Module A's `POST /events` like `feedback.created`. Checked
live: two signals logged in C reached A after `python -m experience_api.privacy outbox`.

Apply on a `c/` branch: `git am module-c-learning-signals.patch` (or `git am -3`), then
`git commit --amend --reset-author --no-edit` so the commit is yours.

### 2. E-mail the weekly digest (PRD 8.3, F7.7)

`GET /reports/digest` returns `{subject, text, items, backlog, second_reviews_pending, send}`.
Module C's mail worker can send `text` once a week to the approvers when `send` is true. The text
holds proposal titles and counts only, never visitors' questions.

### 3. Show the new fields in the approval inbox and quality dashboard

All additive; existing fields are unchanged.

- `GET /improvements` items: `escalation_level` (0 owner, 1 named delegate, 2 College Admin),
  `assigned_to`, `second_review` (`pending`, `agreed`, `disagreed`).
- `GET /improvements?second_review=pending`: proposals waiting for a second approver.
  `POST /improvements/{id}/second-review` `{verdict: agree|disagree, reviewer, note}` (the
  reviewer must not be the person who approved it; A refuses with 409).
- `PUT /learning/settings` accepts `delegates` `{"default": "...", "<department>": "..."}`;
  `peak_season` may now be left out.
- `GET /reports/quality?weeks=12`: per week acceptance rate, implicit signals, proposals opened /
  approved / deployed / rejected, regressions caught; eval score per run (F9.3).
- `GET /evals/runs/{id}` results may carry `judge` verdicts; `POST /evals/runs/{id}/human-grade`
  records a person's verdict on a spot-checked case.

## The guide

- **Contract:** `proposed-knowledge.openapi.json` (sent with this pull request) lists the new
  endpoints: `/evals/voice`, `/evals/comparisons`, `/evals/cases`, `/evals/runs/{id}/judge`,
  `/evals/runs/{id}/human-grade`, `/reports/digest`, `/reports/quality`,
  `/improvements/{id}/second-review`, `/improvements/second-reviews/sample`. Events: add
  `signal.logged` (C → A) to `events.schema.json`.
- **Module A's container image (`deploy/`):** the `tesseract-ocr` package (plus `tesseract-ocr-hin`
  for Hindi scans) for OCR, and the pgvector extension on the shared PostgreSQL. Without them
  Module A still works: scans wait for review and vectors are searched in the API.
- **Model gateway:** the judge uses `local/deepseek-r1-distill-qwen-14b` and
  `local/deepseek-r1-distill-llama-8b` (names in Module B's `gateway/litellm.config.yaml`), only
  in the scheduled window (`KB_JUDGE_WINDOW`, default 22:00-06:00 IST). For GPU memory in model
  comparisons, set `KB_MODEL_STATS_URL` to the Ollama service.
- **Module B** needs nothing new: Module A calls `POST /chat/{id}/messages` with
  `channel: web_voice` and `POST /voice/eval-result`, both already allowed for Module A's key.
