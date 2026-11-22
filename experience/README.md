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
  The widget shows "Connecting you to Admissions…" after a callback request, then the first name of
  whoever claims it (`GET /handoffs/mine`; PRD 13). *Recent decisions* offers **Approve again** on a change the evaluation gate rejected
  (`POST /approvals/decide`); it keeps the answer the approver wrote and runs the tests again.
- **Dashboards (F9.2, F9.3).** For College Admins and officers, `GET /analytics/topics` lists this
  week's unanswered questions (masked by A) and `GET /analytics/quality` gives acceptance, signals,
  proposals and test-set scores per run, week by week. Both say `available: false` when A is down,
  and the page says so instead of showing zeros.
- **Widget Markdown (F1.2).** Bold, italics, code, headings and bullets, built as React elements (no
  HTML), so long answers still collapse after 12 lines.
- `requirements.txt` now declares `pyyaml` and `python-multipart`, which C already imported.

## Hardening pass (PRD 12.3, F10.3, F9.2)

- **Failed sign-ins are slowed down.** After 10 wrong passwords in 15 minutes for one person from
  one network, or 50 from one network across all accounts, `POST /auth/login` answers 429 with
  `Retry-After`. Two-factor codes allow 5 wrong tries per person in 5 minutes. Only failures count,
  so someone who signs in correctly is never slowed down, and a correct sign-in clears their count.
  The counts are kept in memory per API process; they reset on restart.
- **A college goes live without the Colleges page open (F10.3).** The messaging worker checks each
  running launch test. When it passes, the college goes live and `tenant.live` is audited once. A
  failed run is remembered and not asked about again; a new launch starts clean.
- **The overview dashboard asks Module B before opening its database transaction,** so a slow
  Module B never holds a database connection. It also shows answers per model (PRD 9.5).

## Beyond the PRD's letter: console home, shared limits, pooling, drafts

- **Needs you today (PRD 13).** `GET /today` lists what this person should do first, most urgent
  first: callbacks past their promised time, requests nobody has claimed, tasks they claimed, hot
  leads not yet contacted, and approvals waiting for them. Each item links to its page with the
  filter set (`/console?mine=true`, `/console/leads?band=hot&stage=new`). Row-Level Security limits
  every count, so faculty see only their departments. If Module A is down, approvals say "could not
  be loaded", never 0.
- **Rate limits shared by every API process (12.3).** Migration `011_shared_rate_limits.sql` keeps
  the counts in `app.rate_hits`, behind three security-definer functions. RLS is on with no
  policies, so the API role cannot read the rows, and keys are HMACs, so no address or e-mail is
  stored readable.
- **Connection pool.** `db.tx` takes connections from a pool (`psycopg-pool`, `DB_POOL_MAX`,
  default 10, `0` turns it off). RLS settings stay transaction-local, and each returned connection
  is reset, so no college's context reaches the next request. A test checks this.
- **Draft from documents.** On a "No reliable answer" proposal, `POST /approvals/{id}/draft` asks
  Module A to draft the answer from the college's approved documents, with its sources. The
  approver checks and edits it; the evaluation gate still runs.
- **Fewer steps.** A visitor who already agreed to be contacted in this chat, to the current consent
  wording (`GET /consents`), and who already gave their age, goes straight to the contact details;
  the widget says they agreed earlier. A changed wording, a withdrawal or a later "no" asks again.
- **Model usage with tokens (F9.4).** The analytics page shows answers, tokens per answer and total
  tokens per model, from Module B.
- **Accessibility (PRD 13).** An automated WCAG 2.2 A/AA check (axe-core) of the demo site, the open
  widget, sign-in, home, approvals, leads and analytics, in light and dark mode, finds no violations.
  It found and we fixed: brand badges below 4.5:1, secondary buttons that stayed white in dark mode,
  the launcher using the raw college colour (`embed.js` now darkens it to 4.5:1), and a tab list
  that held a checkbox. A screen-reader and keyboard-only pass by a person is still to do.
- **Degraded mode (PRD 10.2, 18).** When the assistant cannot answer (down, or the answer was lost),
  the widget shows the college's approved answers closest to the question, beside the offer of a
  person. `GET /widget/fallback-answers` (visitor token) serves Module A's degraded export, cached
  for 10 minutes per college and still served from the last good copy while Module A is down.
- **Estimated cost per model (F9.4).** With `TOKEN_PRICES_INR` set in Module B, the model usage
  table adds an estimated cost; without it, no figure is shown.

## Extras: staff photo, spoken rating, SMS, browser push

- **Staff photo on the banner (PRD 13).** *Your account* (`/console/account`) lets staff add their
  own photo (`PUT /auth/me/photo`: PNG, JPEG or WebP by the file's own bytes, at most 300 kB;
  migration `012_staff_photos.sql`, RLS: own photo only). The visitor's banner shows it beside the
  first name once that person claims the visitor's own request (`GET /handoffs/mine/photo`).
- **Spoken end-of-chat rating.** While the rating is open, the widget asks the visitor to say a
  number from 1 to 5; the next recording goes to Module B as `expect=rating` and is saved like the
  stars. Nothing is asked of the model.
- **SMS replies (F3.6).** `send_sms` on a staff reply queues it in `app.message_outbox`
  (`channel = 'sms'`) for an adult's own mobile; a minor's guardian is reached by e-mail or WhatsApp.
  With `SMS_API_URL` and `SMS_API_KEY` (any JSON provider: `POST {to, from, text}` with a bearer
  key) the messaging worker sends it, at most three SMS, retried for 24 hours, then forgets the number
  and the text. Without a provider it waits, as e-mail does without SMTP.
- **Browser push (F7.5).** *Your account* turns notifications on for a browser. The push carries no
  content ("Something new is waiting for you in the console"), so no applicant detail goes through
  the push services and no payload encryption is needed. Only the browser vendors' push services can
  be subscribed (no internal addresses). It reaches the same people as the in-console
  notification. Set `VAPID_PRIVATE_KEY_FILE` (`openssl ecparam -name prime256v1 -genkey -noout -out
  vapid.pem`) and `VAPID_SUBJECT`; unset, push is off. Migration `013_browser_push.sql`.
- **The messaging worker runs each step on its own.** Push, launches, escalations, the digest,
  WhatsApp, SMS and e-mail for each college: a failure is logged and listed under `errors` in the
  run's result, and the other steps still run. A database outage no longer ends the worker.

## Kannada (added 9 Nov 2026)

The widget offers **EN · हिं · ಕ**. All of its text is in Kannada (`lib/i18n.ts`), and a question
typed in Kannada script switches the widget to Kannada. The guardian-code request accepts `kn`.
**The Kannada wording was written without a native speaker** and must be reviewed by one before a
real college uses it, above all the consent and guardian text (roadmap item 19).
- **The college's own texts in the visitor's language.** `translations` in the college settings
  (Tenant Pack `translations:`) holds the assistant name, greeting, announcement, suggested
  questions and topic chips in Hindi and Kannada. In Hindi or Kannada the widget never shows an
  English one: a text the college has not translated is left out, and the name and greeting fall
  back to the widget's own wording (`lib/local-tenant.ts`). The greeting follows the language
  switch. The demo college's Kannada suggestions are questions the Kannada assistant answers from
  the catalogue.
- **Feedback that Module A can learn from (November 2026).** After "Wrong", "Outdated" or
  "Incomplete" the widget offers an optional box ("Tell us more", with Skip), so a visitor can say
  what the right information is; "Other" still needs its text. Each `feedback.created` event
  carries `session_fp`, a one-way hash of the visitor's session (never the session id), so Module
  A's self-learning layer can require several distinct visitors to agree before it learns
  anything (`knowledge/docs/self-learning/README.md`).
- **Review answers shows what each rating is about.** `GET /feedback` adds the visitor's question
  and the assistant's answer, from Module B's `POST /messages/lookup` (one call for the whole
  list; phone numbers and e-mail addresses masked by B). `text` says `missing` when B has no such
  answer (erased on request, or a demo record) and `unavailable` when B cannot be reached; the
  page says so. "Correct this answer" starts from the answer the visitor saw.
- **A correction from a callback reply carries the visitor's question** (the last thing the visitor
  said in the task's transcript). Module A turns a correction into a curated answer, which needs
  the question it answers; before this, such corrections could only become "(question not
  recorded)" proposals. Corrections from *Review answers* get the question from Module B.
- **Where each correction is.** A review card with a correction shows its stage on the way to
  visitors: received (waits for the nightly review of feedback), waiting for approval, being
  tested, **live** (visitors now get the corrected answer), failed the tests, rejected, rolled back,
  or not turned into a change. C asks Module A's `GET /improvements/corrections` for the whole
  list in one call; if A cannot be reached, the card says "Correction sent", as before.
- **Approval inbox, newest first.** Proposals are listed by their latest activity (when the newest
  feedback or correction joined them, `last_activity_at` from Module A), so a correction just made
  is on top; "Biggest impact first" is a choice in the page header.
- **Recent decisions, latest first.** On the Approvals page, "Recent decisions" sits right under the
  inbox and lists the newest decision first (`decided_at` from Module A), so an approval just made
  is at the top.
- **Publish now (urgent), College Admin only.** In Approvals, the College Admin can publish a
  change before it is tested: it reaches visitors within seconds, the full test set still runs,
  and a failed test takes it down automatically. It is audited as `improvement.publish_now`.
  Review answers shows "Live, being tested" and, if the test fails, "Taken down". Everyone else's
  approvals still go live only after the test (PRD 8.4).
- **Fee corrections.** The assistant always quotes the official fee list (the catalogue), even
  over a corrected answer. A correction that changes a fee ("the tuition fee is 4,00,000") changes the official fee list
  itself once it is approved and passes the test (the test expects the new fee), so the chat
  quotes it; the Approvals card and the correction form say which fee will change. A figure that
  cannot be tied to one fee is flagged instead ("Send anyway"), asking to name the fee.
- **Test results in the bell.** When an approved change finishes its test, the approvers of its
  area (and the College Admin) get a bell notification: "✅ Live now", "❌ Failed the test, nothing
  changed" or "❌ Failed the test and taken down", with the reason or the fee that changed. Module A
  sends `improvement.tested`; it is kept to the console, never sent to the college's webhooks.
  While a change is being tested, Review answers and Approvals refresh every 20 seconds.
- **Approvals are tested in about 20 seconds.** Module A tests an approved change on the questions
  it can affect (its own, the programme's fee or critical questions, the most similar ones), two
  at a time, measuring "before" only for questions that fail; the whole test set runs in the
  nightly job, and the bell warns if critical questions got worse. `KB_GATE_MODE=full` restores
  the whole-set test per approval.
- **Staff console design.** Neutral sidebar and top bar; the college's brand colour marks only the
  college, the current page and primary actions. One heading scale (`PageHeader`, `SectionTitle`),
  underline tabs, quiet secondary buttons (`btn-quiet`), and status colours (`danger`, `warn`,
  `ok`) that keep 4.5:1 in light and dark. Console-only classes are scoped under `.console`, so
  the college-facing pages are unchanged. The sidebar shows what is waiting per section; the
  Queue is a table sorted by urgency; Knowledge has search and a status filter; Analytics shows
  the PRD targets (resolution ≥ 80%, lead capture ≥ 25%, chat first sentence ≤ 5 s p95).

## Staff roles

PRD 5.2 defines five staff roles. Four more cover how colleges actually staff admissions; they
are additions beyond the PRD (migration `014_more_staff_roles.sql`).

| Role | Who | Queue | Leads | Corrections | Approves | Analytics |
|---|---|---|---|---|---|---|
| College admin (PRD) | Owns the college's set-up | all | edit | yes | everything | all |
| Admission officer (PRD) | Admission desk lead | admission | edit | yes | admission answers | all |
| Faculty (PRD) | Programme coordinator | own departments | linked only | yes | own departments | own queue |
| Marketing (PRD) | Campaigns | — | read, export | — | — | leads and funnel |
| **Admission counsellor** | Front desk, telecalling | admission | edit | suggests | **nothing** | own queue, leads |
| **Finance officer** | Fees, refunds, scholarships | own departments | linked only | yes | own departments | own queue |
| **Hostel warden** | Hostel and accommodation | own departments | linked only | yes | own departments | own queue |
| **Management** | Principal, dean | — | — | — | — | totals, funnel, quality; no personal details |

- **One rule in the database.** Each added role reaches exactly the rows of the PRD role it
  extends, through `app.policy_role()` inside `app.is_staff()`: counsellor → admission officer,
  finance officer and hostel warden → faculty, management → marketing. Every existing RLS policy
  therefore applies unchanged; the API (`deps.POLICY_ROLE`, `DEPARTMENT_ROLES`, `ADMISSION_DESK`)
  then narrows each role, e.g. a counsellor is refused the approval inbox and management the
  lead list.
- **Departments.** A finance officer or hostel warden needs at least one department, like
  faculty, and handles the handoffs Module B routes to it (RUAS's pack already has `Finance` and
  `Hostel`), so Module B needs no change. They count towards the onboarding check "a faculty
  member for every department".
- **Where to set them.** Admin → Staff (the role list explains each role), or `staff:` in a
  Tenant Pack.
