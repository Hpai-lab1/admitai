# Moving this prototype into the team repository

The team repository (`Hpai-lab1/admitai`) is public and has hard rules: each student changes only
their own module, the guide owns `contracts/`, `deploy/`, `eval-data/` and `.github/`, nothing
private is committed, and every change is a reviewed pull request linked to one requirement ID.
This prototype broke the first rule on purpose (all three modules in one place), so it must not
be pushed as it is. It has no remote, and a pre-push hook blocks pushing.

What can move, and by whom:

| Part | Who brings it in | How |
|---|---|---|
| `experience/` (Module C) | Module C's owner | Own pull requests on `c/…` branches (below) |
| `knowledge/` (Module A) | Module A's owner | Offered as a reference; the owner decides what to adopt, through their own `a/…` pull requests |
| `assistant/` (Module B) | Module B's owner | Same, through `b/…` pull requests |
| `docs/proposed-contracts/` | the guide | A proposal for `contracts/` (below); the guide edits and freezes the contracts |
| `scripts/`, `local.env.sh` | the guide | Inputs for `deploy/` (Compose files) and CI; not committed as they are |
| `packs/demo-college/`, `packs/riverside/` | the guide | Synthetic, `.example` e-mails only; the guide-gate check blocks data files, so the guide decides whether they become test fixtures |

## Never moves to the public repository

- `packs/ruas/` and anything from `university-faq-assistant`: real university content and a
  question set drawn from the prototype. It belongs in the private repository, if anywhere.
- `local.secrets.sh`, `.local/` (logs, voice models), any `.env`: secrets and machine-local files.
- Server details beyond `deploy/README.md`: the prototype runs only on 127.0.0.1 and uses the
  ports already assigned there (A 8100, B 8150, C 8200 and 8210).

## Contract changes to propose to the guide

The three stub contracts are fully implemented (`python scripts/check_contracts.py`). The
implementations add endpoints the stubs do not list yet; the full proposals, with request and
response schemas, are in `docs/proposed-contracts/*.openapi.json`, plus `events.schema.json` for
the C → A events the contracts README says are still "to add". Summary:

- **Module A**: document upload/detail/approve-expire-rollback, crawl sync, catalogue list and import,
  curated-answer detail/edit/actions, `POST/GET /events`, proposal detail/decide/analyse,
  eval runs list/detail, weekly report, cut-over, degraded export, Tenant Pack readiness and launch verdict.
- **Module B**: conversation and message views, signals and grounding reviews (for A's learning loop),
  traces, tenant settings and Tenant Pack (agent part), sandbox deletion, WhatsApp subscription check,
  voice turn and speak.
- **Module C**: what Module C already had (auth, widget session, callbacks, handoff actions, guardian
  consent, leads, feedback, approvals, admin, notifications, break-glass), plus
  `POST /channels/sessions` (for B's WhatsApp), approval history and rollback, and the
  `/platform/tenants…` onboarding endpoints, and erasure (`POST /privacy/erase`,
  `POST /leads/{id}/erase`), webhooks (`/webhooks…`, and `POST /webhooks/events` for A's events).
- **All modules**: errors become RFC 9457 problem+json (`detail` stays a string; structured
  problems move to a top-level `problems` member). Callers reading `detail.problems` must change.
- **Module B** also adds `POST /privacy/erase` (called by C only). **Events** add
  `erasure.requested`, which C delivers to A after B has erased the transcripts.
- **Keys** (for `deploy/`): one secret per caller and callee (`KEY_C_TO_A`, `KEY_B_TO_A`,
  `KEY_C_TO_B`, `KEY_A_TO_B`, `KEY_B_TO_C`, `KEY_A_TO_C`, `KEY_OPS`), Module C's RSA private key
  (`JWT_PRIVATE_KEY_FILE`) and its public half for B (`JWT_PUBLIC_KEY_FILE`), B's own
  `VOICE_TOKEN_SECRET`. `SERVICE_KEY`, `JWT_SECRET`, `DEV_ECHO_CODES` and `DEV_MFA_CODE` are
  development-only; the services refuse to start with them unless `APP_ENV=development`.

## Module C: pull requests, in order

The public repo's `c/module-c-foundation` branch holds Module C's earlier, uncommitted work. This
prototype imported exactly that as its first commit (`23399fd`), so everything since is
Module C's change on top of it:

```bash
git -C ~/Projects/admitai-local diff 23399fd -- experience/   # about 27 files, +2,000 lines
```

Suggested pull requests, each with its tests and README update (requirement IDs from Appendix C):

1. **Real Module A and B instead of the mocks (walking skeleton)**: `clients.py` (B's conversation
   record for the handoff package; Module A's own refusal reasons passed to staff), callback
   package in `routes/handoffs.py`, `CallbackIn.language`. Requirements F3.3, F3.4.
2. **Widget: B's events, inline citations, lead card, guardian shortcut**: `widget-api.ts`,
   `Widget.tsx`, `ContactFlow.tsx`, i18n strings, tests. F1.2 (rendering), F5.1, F5.3.
3. **Local voice instead of the browser's speech service**: `lib/voice.ts`, widget voice states.
   F2.1–F2.4 (C's part). This also removes the browser speech API, which sends audio out of the lab.
4. **Approval inbox: evidence, diff, gate results, rollback**: `approvals/page.tsx`,
   `components/console/proposals.tsx`, `routes/approvals.py` history and rollback. F7.7, 8.4.
5. **WhatsApp sessions for Module B**: `POST /channels/sessions` in `routes/auth.py`. F1.11 (C's part).
6. **Plug-and-play onboarding**: migration `005_platform_onboarding.sql`, `routes/platform.py`, preview
   gate in `widget_session`, *Colleges* page, `api.ts` upload fix. F10.1–F10.4.

PRs 1–2 depend on Modules A and B existing in the repository; until they do, they can be tested
against `experience/mocks/` (extended with the new event names) as the README's week-2 plan says.

## Modules A and B

These are complete, tested reference implementations, not something Module C's owner should
commit into `knowledge/` or `assistant/`. Each owner can read them, run their tests
(`python -m pytest -q` in each folder) and bring in what they choose. Natural pull-request
boundaries are the commits of this repository (`git log --oneline`) and the sections of each
module's README.

## Before any of it goes public

- Run the repository's pre-commit hooks and guide-gate locally on each pull request.
- Re-read every file for personal data. This prototype uses only synthetic data and reserved
  `.example` addresses outside `packs/ruas/`, but check anyway.
- Point the services at the lab's model gateway (`LLM_BASE_URL`, `ANSWER_MODEL` and so on) and
  re-run `scripts/check_all.sh`; then switch on Hindi answers (`NATIVE_ANSWER_LANGUAGES=en,hi`)
  and see whether they pass.
