# Module C — Experience & Operations

**Owner:** @ramitharavichandra · **Database schema:** `app` · **PRD:** sections 5, 13, 18, 21.2, Appendix C

**Mission:** everything people see and operate, with identity, consent and audit done properly.

## Scope (Level 1)

- **Identity and governance:** sign-in, roles, MFA, break-glass, Row-Level Security policies, audit log (PRD 5.4, 11.3).
- **Web widget:** chat UI (F1.1, F1.5, F1.7, F1.8), voice controls, orb and captions (F2), WCAG 2.2 AA (13).
- **Staff console (F7.1–F7.5, F7.7):** handoff and callback queues (F3.2, F3.4, F3.6), notifications, conversation view, corrections, impact-sorted approval inbox with batch approval.
- **Consent and leads:** consent records, guardian verification by e-mail or WhatsApp code, withdrawal, lead list, pipeline and export (F5.2, F5.3 verification, F5.5, F6.7).
- **Feedback (F8.1–F8.4):** capture on web and WhatsApp; events to Module A.
- **Admin portal:** tenant configuration, users and roles, knowledge-base screens backed by Module A, Tenant Pack upload and readiness checklist, sandbox preview (F10.4).
- **Analytics (F9.1–F9.4):** overview, topics, quality and platform dashboards.

## Contracts

- **Provides:** `/auth`, `/handoffs`, `/callbacks`, `/leads`, `/consents`, `/feedback`, `/corrections`, `/analytics`, admin APIs — see `contracts/experience.openapi.yaml`.
- **Consumes:** Module A's KB, curated-Q&A, improvement and eval APIs; Module B's chat and voice APIs.
- **Until week 4:** build against Module A's and B's mocks.

## Thesis angle

Staff usability study of the console and approval workflow; consent and accessibility UX for parents.

## Local prototype: plug-and-play onboarding (PRD F10, goal G4)

| Endpoint (Platform Super-Admin) | Purpose |
|---|---|
| `POST /platform/tenants` | Create a college from its Tenant Pack (zip). Module A validates the whole pack first (any problem: nothing is created); then the college is created as `onboarding` with its branding and staff roster (temporary passwords shown once), and Module B gets the agent settings |
| `GET /platform/tenants` | All colleges and their status (no personal data) |
| `GET /platform/tenants/{id}/readiness` | Checklist: fees, dates and eligibility for every program, an owner for every document, no catalogue conflicts, launch test-set size (from Module A); an officer, and faculty for every department (Module C) |
| `POST /platform/tenants/{id}/launch`, `GET .../launch` | Runs the college's test set through Module B; the college goes live only if no blocking readiness item is open, no critical case fails and the pack's thresholds are met (F10.3) |
| `POST /channels/sessions` (Module B only) | A visitor session for WhatsApp, so consent, leads and callbacks belong somewhere |

- While a college is onboarding its widget opens only for its own signed-in staff (the console's *Preview widget* page passes their token): the sandbox preview of F10.4.
- Database rules (migration 005): the Super-Admin may create colleges, list them and change their status, and may add staff only while a college is onboarding; staff of a live college are the College Admin's.
- Console: *Colleges* page for the Super-Admin (upload a pack, readiness, launch).
- Measured: `scripts/onboard_college.py packs/riverside` takes a new synthetic college from zip to live in about a minute (pack load 5 s, launch test about 50 s on a laptop), with no code changes. The PRD asks for 150 real questions before a real launch; the synthetic pack has 20 and sets its own minimum, which the checklist states.

## Local prototype: e-mail and overdue callbacks (PRD F3.4, F3.6, F5.3, F7.5)

`python -m experience_api.messaging run --every 60` (started by `scripts/run_local.sh`) sends queued
e-mail through the college's SMTP server and checks for overdue callbacks. Set `SMTP_HOST`,
`SMTP_PORT` (587), `SMTP_USER`, `SMTP_PASSWORD`, `SMTP_FROM` (and `SMTP_STARTTLS=0` only for a local
test relay); without them nothing is e-mailed and messages wait in `app.message_outbox`.

| What | When | To |
|---|---|---|
| Guardian code (F5.3) | sent at once by `POST /consents/guardian/start`, never stored (only its hash) | the guardian's e-mail |
| Callback acknowledgement with the expected time (F3.4) | `POST /callbacks` | the applicant, or a minor's verified guardian e-mail |
| New task (F7.5) | every handoff or callback; the queue and a console link, never the applicant's words | officers, or the department's faculty; the College Admin if nobody is in that queue |
| Staff reply (F3.6) | `POST /handoffs/{id}/reply` with `send_email: true` (409 if there is no e-mail: phone them) | the applicant or guardian |
| Overdue callback (F3 error handling) | once, when `sla_due_at` has passed: in-app and e-mail reminder to the queue, escalation to the College Admin, an apology to the applicant | as above |

- Without a provider, `POST /consents/guardian/start` answers 503 instead of claiming a code was sent.
  `DEV_ECHO_CODES=1` still shows the code on screen in development.
- The outbox is a queue, not an archive: a sent message loses its address and body. Failed sends are
  retried with backoff for 24 hours. Withdrawing consent or erasure cancels queued mail to that person.
- Peak-season mode (PRD 8.3): `PUT /approvals/peak-season` (College Admin) asks Module A to hold
  lower-severity proposals; the approval inbox says how many are waiting.

## Level 1 completion: WhatsApp, signals, digest, second review, dashboards

What Modules A and B asked of Module C (`knowledge/MODULE-C-CHANGES.md`, `assistant/README.md`
"Still open"), built here. Migration `009_whatsapp_signals_digest.sql` is additive.

- **Guardian code by WhatsApp (F5.3).** `POST /consents/guardian/start` with `channel: whatsapp`
  hands the code to Module B (`POST /channels/whatsapp/guardian-code`). B sends it only when the
  guardian's own number writes to the college, so nobody is messaged unasked. C keeps only the hash.
  The visitor is told which number to write to. If B refuses or is down: 503, never a false "sent".
  Optional `language` (`en`, `hi`).
- **WhatsApp messages through Module B (F3.4, F3.6, F3 errors).** They sit in the same
  `app.message_outbox` with `channel = 'whatsapp'` and go out through `POST /channels/whatsapp/notify`:
  - the callback acknowledgement, when a web visitor asked for WhatsApp;
  - the apology for an overdue callback;
  - a staff reply with `send_whatsapp: true` (409 if there is no number).

  Inside the 24-hour window B sends the text; outside it, only the college's approved template. A
  refusal (opted out, no template, no WhatsApp number) is final: the row forgets the number and the
  text, and the task shows "WhatsApp message not sent … please phone or e-mail". An outage is
  retried with backoff for 24 hours. Callbacks that start on WhatsApp are acknowledged by B itself.
- **Implicit signals (F8.3).** `rephrase`, `talk_to_human`, `voice_hangup` and `barge_in` on
  `POST /conversation-events` also queue a `signal.logged` event for Module A: kind, ids and
  channel, never visitor text.
- **Weekly approval digest (8.3, F7.7).** Once every 7 days per college, the messaging worker asks
  Module A to draw the monthly second-review sample, then e-mails A's `/reports/digest` text to
  active College Admins and admission officers. It sends only when A says there is something to
  approve, and the text holds titles and counts, not visitors' questions. One outbox row per week
  records the run; if A is down, nothing is recorded and the next run tries again.
- **Approval inbox (8.3).** Each proposal shows who it sits with: the owner, a named delegate or the
  College Admin. *Second reviews this month* lists sampled approvals for someone other than the
  first approver (`GET /approvals/second-reviews`, `POST /approvals/{id}/second-review`). College
  Admins name delegates per department (`GET /approvals/settings`, `PUT /approvals/delegates`).
  *Recent decisions* offers **Approve again** on a change the evaluation gate rejected
  (`POST /approvals/decide`); it keeps the answer the approver wrote and runs the tests again.
- **Dashboards (F9.2, F9.3).** For College Admins and officers, `GET /analytics/topics` lists this
  week's unanswered questions (masked by A) and `GET /analytics/quality` gives acceptance, signals,
  proposals and test-set scores per run, week by week. Both say `available: false` when A is down,
  and the page says so instead of showing zeros.
- **Widget Markdown (F1.2).** Bold, italics, code, headings and bullets, built as React elements (no
  HTML), so long answers still collapse after 12 lines.
- `requirements.txt` now declares `pyyaml` and `python-multipart`, which C already imported.
