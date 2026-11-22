# AdmitAI: local prototype (all three modules)

This copy of the monorepo builds Modules A, B and C together on one laptop, following the PRD.
It has **no git remote and pushing is disabled**: the public `admitai` repository only lets each
student change their own module, so nothing here goes back there without a decision about which
module's pull request it belongs to.

## On a new Mac

```bash
git clone https://github.com/ramitharavichandra/admitai-prototype.git admitai-local   # private; ask for access
cd admitai-local
scripts/setup_new_mac.sh    # ~20-30 min, mostly model downloads; safe to run again
scripts/up.sh               # opens the widget and the staff console
```

It needs Homebrew and about 8 GB free, installs PostgreSQL 14, Python 3.12, Node and Ollama if
missing, and creates this Mac's own secrets and keys (they are never in the repository). Pushes go only to that
private repository, and only while GitHub keeps it private: it holds RUAS content that must not
go public.

## Run it

Quickest: `scripts/up.sh` starts whatever is not running and opens the widget and the console.

```bash
source local.env.sh                       # env for all modules; creates git-ignored local.secrets.sh once
python scripts/bootstrap_local.py --fresh # migrations, tenants + staff in C, knowledge in A, agent settings in B
scripts/run_local.sh                      # A :8100, B :8150, C :8200, web :8210 (stop: scripts/run_local.sh stop)
python scripts/e2e_local.py               # walking skeleton: 19 checks across all three modules
python scripts/e2e_learning.py --rollback # full improvement cycle (a few minutes): correction -> gate -> live -> rollback
python scripts/e2e_channels.py            # WhatsApp webhook, voice turn, grounding review
python scripts/onboard_college.py packs/riverside   # a new college from its Tenant Pack, zip to live (G4)
python scripts/suite_prd20.py             # PRD 20: routing, multi-intent, minors, red team, channels, load
scripts/nightly_local.sh                  # nightly jobs: grounding review (B), then Feedback Analyst (A)
scripts/check_all.sh [--quick]            # everything: unit suites, lint, typecheck, all live checks, answer quality
```

Needs: PostgreSQL on 127.0.0.1:5440 (its data is in `~/Projects/.admitai-pgdata`; `scripts/run_local.sh`
starts it after a reboot) (databases `admitai_local`, `admitai_local_test`), Ollama with
`qwen2.5:3b`, `nomic-embed-text` and `bge-m3`, Python 3.12 (`.venv`), Node 22, and for voice the
Whisper `base` and Piper `en_US-lessac-medium` models in `.local/models` (see `assistant/README.md`).

Open http://127.0.0.1:8210/demo-site for the widget and http://127.0.0.1:8210/console for staff
(college `demo-college` or `ruas`; e-mails are printed by the bootstrap; the password is
`DEMO_PASSWORD` in `local.secrets.sh`). Guardian codes are shown on screen (`DEV_ECHO_CODES=1`).
Admins' two-factor code is `123456` locally (`DEV_MFA_CODE`, accepted only with `DEV_ECHO_CODES=1`;
real authenticator codes also work). Never set either variable on a real server.

## Service trust and privacy

- **Keys between modules.** Each caller has its own key per callee (`KEY_C_TO_A`, `KEY_B_TO_A`,
  `KEY_C_TO_B`, `KEY_A_TO_B`, `KEY_B_TO_C`), and each key reaches only the endpoints that caller
  needs: Module B may only search Module A and read its catalogue, for example. Operator scripts
  use `KEY_OPS`. All of them are generated into `local.secrets.sh`.
- **Visitor tokens** are signed by Module C with an RSA key (`.local/keys/visitor-jwt.pem`); Module
  B holds only the public key, so it can check a token but never mint one.
- **`APP_ENV`** is `production` unless set. `local.env.sh` sets `development`, which is the only
  mode that accepts the shared `SERVICE_KEY`/`JWT_SECRET` fallbacks and the dev flags above. In
  production each module refuses to start if a key is missing or a dev setting is present.
- **Erasure on request** (PRD 11.4): "Delete my data" in the widget, `DELETE MY DATA` on WhatsApp,
  or a College Admin on the lead (`POST /leads/{id}/erase`). Module C blanks its rows and queues
  the request; delivery asks Module B to blank the transcripts and then Module A to strip the
  learning signals. Anonymous counts stay. A module that is down catches up at the nightly run.
- **Retention** runs nightly (`scripts/nightly_local.sh`): transcripts after
  `retention.transcript_days` (default 730), leads inactive for `retention.leads_inactive_days`
  (default 548), and audit rows after 3 years. Voice audio is never stored.
- Module A masks e-mails, phone numbers, ID numbers and self-introduced names before a signal is
  stored. Regex masking is a floor, not a guarantee; erasure covers what it misses.

## Errors, limits and webhooks (PRD 12.2, 12.3)

- Every error from every module is `application/problem+json` (RFC 9457) with a readable
  `detail` and a `trace_id`, also in the `X-Trace-Id` header; send `X-Trace-Id` to follow one
  request across modules. Unexpected errors are logged under that id and never show internals.
- Visitors are limited per session (in the chat), per IP and per college (429 with
  `Retry-After`). `local.env.sh` raises the per-IP limits because every test runs from
  127.0.0.1. Set `TRUST_PROXY_HEADERS=1` only behind a proxy you run.
- **Webhooks**: Console → Admin → Integrations. The worker (`python -m experience_api.webhooks run
  --every 15`, started by `scripts/run_local.sh`) signs and sends deliveries. Outside development,
  only https URLs on public addresses are accepted, checked again before each delivery.
  `python scripts/e2e_webhooks.py` checks it live with a receiver on port 8231.

## Measuring answers honestly

- **Groundedness** in every evaluation run (and in the gate for each proposed change) is the
  share of factual claims in the answers that Module B's grounding review finds supported by the
  cited source text. A claim that adds no key word, keeps every number in its source line and is
  not negated is supported without a model; the rest go to the quote test on the model. Answers
  unchanged since the baseline are not reviewed twice. If the review cannot run, the run reports
  it and the gate fails rather than skipping the metric. (Before Day 9, "groundedness" only meant
  "no forbidden source cited"; that is now `forbidden_free`.)
- The review is a lower bound on a 3B judge: it misses paraphrases such as "two years" for "24
  months". It also flags real over-claims, such as "for first-year students" when the source
  gives the fee without that qualifier.
- **Analytics** count WhatsApp conversations alongside the widget's, and show answer outcomes by
  channel. Conversations started by Module A's evaluation runs or operator scripts are recorded
  as such in Module B and left out of analytics and learning signals. The local test scripts
  behave like real widget visitors and *are* counted; on a real server, run them against a test
  college.

## Tenants

| Slug | Content | Widget key |
|---|---|---|
| `demo-college` | Synthetic engineering college: catalogue, 5 documents (one expired), curated answers | `pk_demo_college_0001` |
| `ruas` | 48 FAQs from the university-faq-assistant prototype (SAMPLE placeholders as drafts) | `pk_ruas_local_0001` |
| `other-college` | Empty second tenant used by Module C's isolation tests | |
| `riverside` | Synthetic commerce college onboarded through the platform API, not the bootstrap | generated at onboarding |

Each tenant is described by a Tenant Pack in `packs/<slug>/` (PRD F10.1); every module loads its
own part of it.

## Differences from the lab set-up (all configuration-level)

| PRD (lab) | Here | Change back by |
|---|---|---|
| Qwen2.5-14B answers, Llama-3.1-8B routes, via LiteLLM | `qwen2.5:3b` for both, Ollama's OpenAI-compatible API | `MODEL_ALIASES` (the names a pack's `model_policy` may use → gateway names), `ROUTER_MODEL`, `LLM_BASE_URL`; `ANSWER_MODEL`/`FALLBACK_MODEL` are the defaults for packs without a policy |
| Hindi answers | English answer + note for Hindi/Kannada questions (3B Hindi was garbled) | `NATIVE_ANSWER_LANGUAGES=en,hi` |
| pgvector HNSW | `real[]` vectors searched in Module A | a migration in `knowledge/db/migrations` |
| Supabase auth, Langfuse, Redis | Module C's own JWT auth, `chat.traces`, in-process rate limits | |
| Docker Compose, Cloudflare | `scripts/run_local.sh` on 127.0.0.1 | |
