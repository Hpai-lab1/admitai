# AdmitAI improvement roadmap (third-order review)

This roadmap follows the [PRD gap analysis](prd-gap-analysis.md). The gap analysis asks whether
the code does what the PRD says. This document asks what should change next, including where
the PRD or the current design is not the best choice. Every improvement below is still to do;
what earlier passes already built is listed at the end, so nothing is proposed twice.

Last updated 9 Nov 2026, after gap-analysis pass 11. Items 20 and 21 come from the [product review](product-review.md).

## How the items are ranked

Each item is scored on three things:

- **Impact:** how much it helps applicants, staff or the college.
- **Feasibility:** whether it can be built now, or waits on the lab servers, the guide, the team
  or an outside party.
- **Risk:** what goes wrong if it is left undone.

| Priority | Meaning |
|---|---|
| **P0** | Before the first real college goes live |
| **P1** | During the pilot term |
| **P2** | When scale or evidence calls for it; not before |

**Source** says whether the PRD requires the item, or whether this review recommends it.

## Summary

| # | Improvement | Priority | Source | Complexity | Waits on |
|---|---|---|---|---|---|
| 1 | ~~Never judge an applicant's eligibility from their age or class~~ **Built 9 Nov** | P0 | Recommendation (safety) | Low | — |
| 2 | Keyboard and screen-reader check by a person | P0 | PRD 13 | Low | A tester |
| 3 | Agree the real API contracts, and check them in CI | P0 | PRD 12, repo rules | Medium | Guide |
| 4 | Version the APIs (`/v1`) before the first college | P0 | PRD 12.3 | Low | Guide |
| 5 | Each college's own held-out test questions | P0 | PRD 20 | Medium | Colleges |
| 6 | Catalogue answers in Hindi, and for dates and eligibility | P1 | Recommendation | Medium | — |
| 7 | Hindi answers from the larger lab model | P1 | PRD F1.6 | Low | Lab |
| 8 | Alerts and monitoring | P1 | PRD 16 | Medium | Lab, guide |
| 9 | Deployment, backups and the degraded edge page | P1 | PRD 10, 18 | Medium | Guide |
| 10 | One shared package for common code | P1 | Recommendation | Medium | Team |
| 11 | Hands-free voice and live captions (LiveKit worker) | P1 | PRD F2.1, F2.2 | High | Lab |
| 12 | GPU use and a real price per college | P1 | PRD F9.4 | Medium | Lab |
| 13 | WhatsApp business verification and templates | P1 | PRD F1.11 | Low (code) | Meta |
| 14 | An SMS provider | P2 | PRD F3.6 | Low (code) | Provider contract |
| 15 | ~~Measure the widget on 4G before any rewrite~~ **Measured 9 Nov: 2.5 s on Slow 4G, no rewrite needed** | P2 | PRD F1.1 | Low | — |
| 16 | A job queue for background work | P2 | Recommendation | Medium | More than one API process |
| 17 | Signed tokens or mutual TLS between modules | P2 | Recommendation | Medium | Lab |
| 18 | Cheaper sandboxes for large knowledge bases | P2 | Recommendation | High | Large colleges |
| 19 | Native review of the Kannada and Hindi wording | P0 for a college using that language | Recommendation | Low | A native speaker |
| 20 | Count visitors being answered across all Module B processes | P1 | Recommendation | Low | Before more than one B process |
| 21 | Split the 900-line widget file into parts | P1 | Recommendation | Medium | — |
| 22 | Bind numbers to their labels in Indic-script sentences before any native Hindi or Kannada answers | P0 before native answers | Recommendation (safety) | Medium | A native speaker for each language |
| 23 | Stop irrelevant answers to Kannada questions sent to the model | P1 | Recommendation | Medium | A Kannada test set |
| 24 | A speech model trained on Indian languages for Kannada voice | P0 for Kannada voice | Recommendation | Medium | Lab GPUs |

Items 1 to 5 are P0 because getting them wrong harms an applicant, locks the system out of real
use, or makes a later change break colleges that are already live.

## P0: before the first real college

### 1. Never judge an applicant's eligibility from their age or class

> **Built on 9 Nov 2026** (gap-analysis pass 10): `checks.VERDICT_PATTERNS` in Module B, the
> prompt `answer-v5`, a critical test question (`d26`) in the demo college's test set, and tests.

- **Current limitation:** asked "I am 16 and in class 11. What is the hostel fee?", the model
  once added "As you are in class 11, you are not eligible for a seat in our college." The
  sentence checks blocked it, because it cited nothing. But the visitor then saw "I'm not certain
  about that detail" after a correct fee, and a cited version of the same claim would pass.
- **Proposed solution:** add a check in Module B that blocks any sentence giving an eligibility
  verdict about the visitor ("you are (not) eligible", "you can't apply", "you qualify"), cited
  or not. Tell the model in the prompt to state the published criteria instead. Add these
  questions to every college's test set as critical cases.
- **Why it matters:** a wrong "you are not eligible" can turn a student away for good, and it is
  most likely to be said to under-18s, the group the PRD protects most (F5.3).
- **Expected user benefit:** applicants get the criteria and decide for themselves, or are
  offered a person. They never get a verdict from a machine.
- **Technical impact:** one rule in `assistant_api/checks.py`, one prompt line, and test cases.
  No other module changes.
- **Dependencies:** none.
- **Implementation complexity:** low (about a day, with tests).
- **Priority:** P0.
- **Source:** additional recommendation. It strengthens PRD F1.4 (sentence checks) and F5.3
  (minors).

### 2. Keyboard and screen-reader check by a person

> **Prepared on 9 Nov 2026:** the step-by-step script is in
> [accessibility-test-script.md](accessibility-test-script.md); it still needs a person to run it.

- **Current limitation:** an automated WCAG 2.2 A/AA check (axe-core) of 7 pages, in light and
  dark mode, finds no violations. But automated checks catch only about a third of accessibility
  problems: they cannot tell whether focus order makes sense, whether streamed answers are read
  well, or whether the voice orb's states are announced.
- **Proposed solution:** one tester goes through a scripted session with VoiceOver (macOS and
  iOS) and NVDA (Windows): ask a question, follow a citation, request a callback, go through the
  under-18 guardian flow, rate the chat. They use only the keyboard, and record each problem with
  its WCAG criterion.
- **Why it matters:** the PRD requires WCAG 2.2 AA (section 13). Colleges, often public bodies,
  are asked about it, and applicants with disabilities depend on it.
- **Expected user benefit:** blind and keyboard-only applicants can complete every flow.
- **Technical impact:** small fixes in `experience/web` (focus handling, live-region wording).
- **Dependencies:** a tester with a screen reader; about half a day.
- **Implementation complexity:** low.
- **Priority:** P0.
- **Source:** required by the PRD (13).

### 3. Agree the real API contracts, and check them in CI

- **Current limitation:** the agreed contract for Module C lists 8 endpoints; the code provides 61
  (Module B 25, Module A 39). The repository rule is that "the contract wins", but in practice the
  contracts describe a small part of what the modules actually call. `scripts/check_contracts.py`
  generates the real specification into `docs/proposed-contracts/`, but nothing enforces it.
- **Proposed solution:** the guide reviews the generated specifications and adopts them, with
  changes, as the new `contracts/`. CI then fails a pull request when a module's API differs from
  its contract, so a change has to be agreed first.
- **Why it matters:** three people change three services that call each other. Without
  contracts that are enforced, a renamed field breaks another module silently, and it is found
  in the demo, not in review.
- **Expected user benefit:** fewer broken flows between the widget, the assistant and the console.
- **Technical impact:** `contracts/` files, and one CI job in `.github/` (guide-owned).
- **Dependencies:** the guide's review and approval.
- **Implementation complexity:** medium (mostly review time).
- **Priority:** P0.
- **Source:** required by the PRD (12, API-first) and the repository rules.

### 4. Version the APIs (`/v1`) before the first college

- **Current limitation:** the URLs carry no version. Once a college has the widget's `<script>`
  tag on its site, any incompatible change to the widget's API breaks that site.
- **Proposed solution:** serve every route under `/v1` as well as the current path for one
  release, move the widget, console and modules to `/v1`, then drop the unversioned paths.
- **Why it matters:** it is cheap now, with no college live, and expensive afterwards.
- **Expected user benefit:** colleges' sites keep working through upgrades.
- **Technical impact:** a router prefix in each module, and base URLs in the web app and clients.
- **Dependencies:** the guide's decision (it changes the contracts, item 3).
- **Implementation complexity:** low.
- **Priority:** P0.
- **Source:** required by the PRD (12.3).

### 5. Each college's own held-out test questions

- **Current limitation:** the evaluation gate and the launch test use small synthetic test sets
  (about 20 to 70 questions per college). The PRD asks for 150 real questions before a real launch.
  On a small set, one question flipping moves accuracy by more than a point, so the gate can
  wrongly pass or block a change.
- **Proposed solution:** with each pilot college, collect at least 150 real, anonymised questions
  from its admissions inbox, covering fees, dates, eligibility, hostel, documents and sensitive
  topics, with expected facts and sources. Keep a held-out part the team never tunes against.
- **Why it matters:** the gate is the protection against a bad change reaching applicants, and it
  is only as good as its questions.
- **Expected user benefit:** fewer wrong or needlessly refused answers after each change.
- **Technical impact:** data in `learn.eval_cases` through the Tenant Pack; no code change.
- **Dependencies:** the pilot colleges; consent and anonymisation (`eval-data/` rules).
- **Implementation complexity:** medium (collection and labelling effort).
- **Priority:** P0.
- **Source:** required by the PRD (20).

## P1: during the pilot term

### 6. Catalogue answers in Hindi, and for dates and eligibility

> **Partly built on 9 Nov 2026:** Kannada templates for fees and duration, admission dates in
> English and Kannada, and fees every programme shares. Hindi and eligibility remain.

- **Current limitation:** plain English fee and duration questions about one programme are
  answered straight from the catalogue in under a second (`assistant_api/direct.py`). Hindi
  questions, dates and eligibility still go to the model: 9 to 12 s on a laptop, with an
  occasional added sentence.
- **Proposed solution:** add Hindi templates for the same answers, and templates for programme
  dates ("the last date to apply is 30 June 2027") and for published eligibility text, quoted as
  stored. Keep the same narrow rules: anything that needs judgement goes to the model.
- **Why it matters:** fees, dates and eligibility are most of what applicants ask, and Hindi
  speakers are the users the 3B model serves worst.
- **Expected user benefit:** instant, exact answers in the applicant's language.
- **Technical impact:** `direct.py`, its tests, and translated templates checked by a Hindi speaker.
- **Dependencies:** a Hindi speaker to review the wording.
- **Implementation complexity:** medium.
- **Priority:** P1.
- **Source:** additional recommendation. It serves PRD F1.6 (Hindi) and 7.5 (latency).

### 7. Hindi answers from the larger lab model

- **Current limitation:** on the laptop's 3B model, Hindi answers came out garbled, so Hindi
  questions get English answers with a note (`NATIVE_ANSWER_LANGUAGES`).
- **Proposed solution:** on the lab servers, run the Hindi part of the test set on the 14B model.
  If it passes the same checks, add `hi` to the native answer languages for that model.
- **Why it matters:** an English answer to a Hindi question fails the families who most need help.
- **Expected user benefit:** answers in Hindi.
- **Technical impact:** configuration, plus a Hindi test run.
- **Dependencies:** the lab GPUs and the 14B model.
- **Implementation complexity:** low.
- **Priority:** P1.
- **Source:** required by the PRD (F1.6).

### 8. Alerts and monitoring

- **Current limitation:** every turn is traced in `chat.traces`, and the dashboards show quality
  and latency. But nothing alerts anyone when the assistant stops answering, the gate keeps
  failing, or the messaging worker reports errors (it now lists them under `errors` instead of
  stopping).
- **Proposed solution:** export a few health metrics (answers per minute, refusal rate, first
  sentence p95, outbox backlog, worker errors) to the lab's monitoring, with alerts to the on-call
  person. Connect the traces to Langfuse where the lab runs it.
- **Why it matters:** in the busiest weeks, an outage that nobody notices costs applications.
- **Expected user benefit:** problems are fixed before applicants notice.
- **Technical impact:** a metrics endpoint per module; alert rules on the lab.
- **Dependencies:** the lab's monitoring stack; the guide.
- **Implementation complexity:** medium.
- **Priority:** P1.
- **Source:** required by the PRD (16).

### 9. Deployment, backups and the degraded edge page

> **Started on 9 Nov 2026:** the demo can back up and restore its database
> (`demo.sh backup`, `demo.sh restore`, tested by restoring into a throw-away database). The
> production parts (Compose, nightly backups, the edge page) still wait on the guide.

- **Current limitation:** the system runs from local scripts and a demo Docker Compose. Degraded
  mode now exists inside the widget (it shows approved answers when the assistant is down), but
  if the whole server is down, the widget cannot load at all.
- **Proposed solution:** a production Compose file with restart policies; nightly database
  backups with a tested restore; a static fallback page per college (contact details and top
  approved answers from Module A's degraded export) served from the edge.
- **Why it matters:** the PRD's availability target cannot be met without backups and a fallback.
- **Expected user benefit:** applicants still get contact details and basic answers in an outage.
- **Technical impact:** `deploy/` (guide-owned); a small export job.
- **Dependencies:** the guide; the lab's edge or reverse proxy.
- **Implementation complexity:** medium.
- **Priority:** P1.
- **Source:** required by the PRD (10, 18).

### 10. One shared package for common code

- **Current limitation:** the database and pool code, the error format (problem+json), service
  sign-in and rate limiting are copied into each of the three modules. The connection pool had to
  be written three times, and the copies can drift apart.
- **Proposed solution:** a small `common/` package, installed in all three images, that holds
  only infrastructure code. Each module's logic stays in its own folder.
- **Why it matters:** a security or reliability fix is made once, not three times.
- **Expected user benefit:** indirect: fewer bugs that differ between modules.
- **Technical impact:** moving code and updating imports; a change to the module boundaries.
- **Dependencies:** the team's agreement, because it crosses module ownership.
- **Implementation complexity:** medium.
- **Priority:** P1.
- **Source:** additional recommendation.

### 11. Hands-free voice and live captions (LiveKit worker)

- **Current limitation:** voice is push-to-talk over HTTP: press, speak, press, wait. The PRD asks
  for hands-free conversation with live captions. Module B already issues LiveKit room tokens,
  but no worker joins the room.
- **Proposed solution:** a LiveKit agent worker on the lab servers that streams speech to text,
  sends finished questions to the existing engine, and streams the spoken answer back, with
  partial captions in the widget.
- **Why it matters:** voice is how many first-generation applicants and parents prefer to ask.
- **Expected user benefit:** natural conversation; captions for deaf or hard-of-hearing users.
- **Technical impact:** a new worker service; changes to the widget's voice mode.
- **Dependencies:** the lab GPUs and a LiveKit server.
- **Implementation complexity:** high.
- **Priority:** P1.
- **Source:** required by the PRD (F2.1, F2.2).

### 12. GPU use and a real price per college

- **Current limitation:** each answer now records its tokens, and the dashboard can show an
  estimated cost from a configured price (`TOKEN_PRICES_INR`). But local models have no bill per
  token, and nobody has measured GPU time per college, so the price is a guess.
- **Proposed solution:** on the lab servers, measure GPU seconds per 1,000 tokens for each model
  under load, turn that into a price, and set it.
- **Why it matters:** the PRD asks for pricing based on evidence before colleges are charged.
- **Expected user benefit:** for colleges, a fair, explainable price.
- **Technical impact:** a measurement script; configuration only.
- **Dependencies:** the lab GPUs.
- **Implementation complexity:** medium.
- **Priority:** P1.
- **Source:** required by the PRD (F9.4).

### 13. WhatsApp business verification and templates

- **Current limitation:** WhatsApp works inside Meta's 24-hour window. Outside it, only approved
  templates may be sent, and the college has none yet, so such messages are refused (and the
  staff task says so).
- **Proposed solution:** each pilot college completes Meta business verification and gets its
  callback, reminder and staff-reply templates approved; they go in its Tenant Pack.
- **Why it matters:** callbacks often happen more than a day after the applicant wrote.
- **Expected user benefit:** applicants hear back on WhatsApp even after a day.
- **Technical impact:** configuration only; the code is built.
- **Dependencies:** Meta; each college.
- **Implementation complexity:** low (code), with calendar time for approval.
- **Priority:** P1.
- **Source:** required by the PRD (F1.11).

## P2: when scale or evidence calls for it

### 14. An SMS provider

- **Current limitation:** staff can choose "send by SMS", and the messages wait in the outbox,
  but no provider is contracted, so nothing is sent.
- **Proposed solution:** contract a provider registered for India's DLT rules, and set
  `SMS_API_URL` and `SMS_API_KEY` (any JSON provider works).
- **Why it matters:** some applicants have no WhatsApp and do not read e-mail.
- **Expected user benefit:** replies reach them by text.
- **Technical impact:** configuration only.
- **Dependencies:** a provider contract and DLT template registration.
- **Implementation complexity:** low.
- **Priority:** P2.
- **Source:** required by the PRD (F3.6, as an option).

### 15. Measure the widget on 4G before any rewrite

> **Measured on 9 Nov 2026** (phone CPU slowed 4×): on Chrome's "Slow 4G" (1.6 Mbps, 150 ms), the
> widget is ready to type in **2.5 s** on a first visit (282 kB) and 0.5 s on a return visit
> (2 kB); on "Fast 4G", 0.9 s and 0.2 s. The college's own page loads only the 3.4 kB launcher,
> and the widget only when the visitor opens it. **No rewrite is needed**; measure again if the
> widget grows.

- **Current limitation:** the PRD asks for a Preact web component under 60 kB. The widget is a
  Next.js page in an iframe. That is heavier, but it keeps the college's styles and scripts apart
  from ours, which is safer. Its load time on a slow connection has not been measured.
- **Proposed solution:** measure first-load time and size on a throttled 4G profile. Only if it
  misses the PRD's target, slim the widget page (fewer dependencies, no unused framework code).
  Keep the iframe; a full Preact rewrite is the last resort.
- **Why it matters:** many applicants use mobile data, and a slow widget is a widget nobody opens.
- **Expected user benefit:** a widget that opens quickly on any phone.
- **Technical impact:** measurement, then targeted changes in `experience/web`.
- **Dependencies:** none for measuring.
- **Implementation complexity:** low to measure; high for a rewrite.
- **Priority:** P2 until the measurement says otherwise.
- **Source:** required by the PRD (F1.1); this review recommends measuring before rewriting.

### 16. A job queue for background work

- **Current limitation:** evaluation gates run as background tasks inside the knowledge API
  process. A restart interrupts them; recovery on startup and a one-at-a-time database lock
  already exist.
- **Proposed solution:** when more than one API process runs, move gates and other long jobs to
  a small queue (a database table with workers is enough; no new infrastructure).
- **Why it matters:** with several processes, jobs must not be lost or run twice.
- **Expected user benefit:** indirect: approvals that never get stuck.
- **Technical impact:** Module A's job handling.
- **Dependencies:** a decision to run more than one process.
- **Implementation complexity:** medium.
- **Priority:** P2.
- **Source:** additional recommendation.

### 17. Signed tokens or mutual TLS between modules

- **Current limitation:** the modules call each other with static shared keys. Inside one Docker
  network that is acceptable for Level 1.
- **Proposed solution:** short-lived signed service tokens, or mutual TLS, when the modules run
  on separate hosts.
- **Why it matters:** a leaked static key gives access until someone changes it by hand.
- **Expected user benefit:** indirect: safer applicant data.
- **Technical impact:** service sign-in in all three modules (easier after item 10).
- **Dependencies:** the lab's network layout.
- **Implementation complexity:** medium.
- **Priority:** P2.
- **Source:** additional recommendation.

### 18. Cheaper sandboxes for large knowledge bases

- **Current limitation:** each approval copies the whole college (documents, chunks, answers,
  catalogue) into a sandbox before testing. That is fast for today's colleges.
- **Proposed solution:** when a college has thousands of documents, apply the change as an
  overlay on the real data that only the evaluation can see, instead of copying.
- **Why it matters:** copying time and disk grow with the college.
- **Expected user benefit:** for staff, approvals that stay fast as the college grows.
- **Technical impact:** a significant change to Module A's gate.
- **Dependencies:** a large college to measure against.
- **Implementation complexity:** high.
- **Priority:** P2.
- **Source:** additional recommendation.

### 19. Native review of the Kannada and Hindi wording

- **Current limitation:** Kannada was added on 9 Nov 2026 to the widget, the fixed replies,
  WhatsApp and the safety checks, and the Hindi text predates it. Both were written without a
  native speaker. The consent, guardian and erasure texts in particular carry legal weight.
- **Proposed solution:** a native speaker of each language reviews `experience/web/src/lib/i18n.ts`,
  `assistant_api/prompts.py` (`MESSAGES`, `ENGLISH_ONLY_NOTE`), `assistant_api/whatsapp.py`
  (`TEXT`) and the Kannada and Hindi patterns in `checks.py` and `router.py`, then corrects them in
  one pull request.
- **Why it matters:** consent given on wording the visitor misunderstood is not informed consent
  (DPDP), and awkward wording makes the college look careless.
- **Expected user benefit:** clear, natural text in the visitor's own language.
- **Technical impact:** text changes only.
- **Dependencies:** a native speaker for each language; about two hours each.
- **Implementation complexity:** low.
- **Priority:** P0 before a college uses that language with real applicants.
- **Source:** additional recommendation. It supports PRD F1.6 and the consent rules (F5).

### 22. Bind numbers to their labels in Hindi and Kannada sentences

- **Current limitation:** found on 9 Nov 2026. With Kannada answers switched on, the 3B model
  answered "What is the B.Tech CSE fee?" with "90000 INR", the hostel fee. The sentence checks tie
  each number to its label ("tuition fee", "hostel fee", the programme), but only for English
  words; in Indic script they only check that the number appears in a cited source, and 90000
  does. The answers were also garbled.
- **Proposed solution:** add Kannada and Hindi forms of the fee, date and programme labels to the
  fact binding in `checks.py`, with the same swap tests as for English (every pair of real
  catalogue values swapped must be blocked). Until then, keep `NATIVE_ANSWER_LANGUAGES=en`; the
  safe way to give Kannada answers sooner is item 6 (catalogue answers from templates).
- **Also found (9 Nov 2026):** translating the checked English answer instead does not help on the
  3B model. The translations kept every number, so the number check passed them, but the Kannada
  was meaningless. Translation is built (`TRANSLATED_ANSWER_LANGUAGES`) but off; turn it on only
  on a model that passes a Kannada test read by a native speaker.
- **Why it matters:** a wrong fee with a citation looks trustworthy, and it reaches the families
  the PRD most wants to serve.
- **Expected user benefit:** native-language answers that are as safe as English ones.
- **Technical impact:** `checks.py` and its tests in Module B.
- **Dependencies:** a native speaker of each language to list the label words.
- **Implementation complexity:** medium.
- **Priority:** P0 before native answers in any Indic language.
- **Source:** additional recommendation (safety); a condition for PRD F1.6.

### 23. Stop irrelevant answers to Kannada questions sent to the model

- **Current limitation:** found on 9 Nov 2026. A Kannada question about scholarships (which needs
  judgement, so it goes to the model) got an English answer about lateral entry. The multilingual
  search returned the wrong passage, and the relevance check, which compares English words, did
  not catch it.
- **Proposed solution:** for Indic-script questions, translate the question to English with the
  model before searching and before the relevance check (one short call, within the two-call
  budget when the router uses rules), or check relevance on the multilingual embedding score.
  Measure on a Kannada test set of 30-50 questions.
- **Why it matters:** an answer to a different question misleads as much as a wrong fee.
- **Expected user benefit:** Kannada visitors get answers to what they asked, or an honest
  "I don't know".
- **Technical impact:** Module B's retrieval and relevance step; Module A's multilingual search.
- **Dependencies:** a Kannada test set; a native speaker to label it.
- **Implementation complexity:** medium.
- **Priority:** P1 (P0 for a college that expects many Kannada questions).
- **Source:** additional recommendation; serves PRD F1.6.

### 24. A speech model trained on Indian languages for Kannada voice

- **Current limitation:** found on 10 Nov 2026. Whisper `base`, even when told the speech is
  Kannada, wrote one spoken question in Urdu script and another in English letters ("Arji Salislu
  Kone Yadinaam" for ಅರ್ಜಿ ಸಲ್ಲಿಸಲು ಕೊನೆಯ ದಿನಾಂಕ). Whisper `small` took 68 s per question inside
  Docker on the Mac and was no better. Today the assistant copes: a transcript in the wrong
  script is asked again, and the common questions are recognised in English letters too.
- **Proposed solution:** on the lab GPUs, use a speech model trained on Indian languages (for
  example AI4Bharat's IndicConformer or IndicWhisper) for Kannada, or Whisper `large-v3`; measure
  word error and latency on 50 recorded Kannada questions from real parents (with consent).
- **Why it matters:** many parents prefer to speak, and the PRD's voice channel is meant for them.
- **Expected user benefit:** a Kannada-speaking parent is understood the first time.
- **Technical impact:** a model choice in `voice.py` per language; no change elsewhere.
- **Dependencies:** the lab GPUs; consented Kannada recordings for the test.
- **Implementation complexity:** medium.
- **Priority:** P0 before offering Kannada voice to real parents.
- **Source:** additional recommendation; serves PRD F2 and F1.6.

## What to build now, and what needs approval

Not everything here should be built at once. Each item is in one of four groups:

| Group | Items | Decision |
|---|---|---|
| Essential for product quality | 1, 2, 3, 4, 5, 9 | 1 built; 2 prepared (needs a tester); 9 started (demo backups); 3, 4 and 5 need the guide or the colleges |
| Possible without changing the agreed scope | 2 (script), 9 (demo backups), 15 (measurement) | Done on 9 Nov 2026 |
| Needs approval or backend support | 3, 4 (guide); 6 (changes Module B's answers: its owner, and a Hindi reviewer); 7, 11, 12 (lab); 8 (lab monitoring); 10 (team); 13 (Meta); 14 (provider) | Explained here; not built until approved |
| Postponed | 14, 16, 17, 18; the rewrite in 15 | Until scale or measurements call for them |

Changes that alter how the product behaves, change the architecture, touch another person's
module, or widen the agreed scope are described here first and built only after approval.

## Decisions reviewed and kept

These were challenged and found sound. Changing them would add complexity without a clear gain.

| Decision | Why it stays |
|---|---|
| One database with a schema per module and Row-Level Security | Simpler to run and back up than three databases; isolation is enforced, and tested, by RLS |
| Polling for the handoff banner and notifications | At Level 1 scale, WebSockets would add moving parts for no visible gain |
| Refusing rather than guessing when a sentence cannot be checked | Correct for admissions; the fix is better input (catalogue answers, item 6), not looser checks |
| Module A's in-memory search index | It checks a database fingerprint on every search, so every process sees changes at once |
| Three modules owned by three people | It matches the team; reduce the copied code instead (item 10) |

## Already built in passes 4 to 9

For traceability: these came out of the same review and are done. Details are in the gap
analysis and each module's README.

| Improvement | Module | Pass |
|---|---|---|
| Failed sign-ins and two-factor codes limited, shared by every process | C | 4, 5 |
| A college goes live without the Colleges page open | C | 4 |
| One more try when a sentence lacks a source number, within the two-call budget | B | 4, 9 |
| Shared fees said once ("for every program") | B | 4 |
| "Needs you today" console home | C | 5 |
| Connection pools in all three modules | A, B, C | 5 |
| Draft a missing answer from the college's documents | A, C | 5 |
| Evaluation runs one at a time and wait while visitors are answered | A, B | 5 |
| WhatsApp sources kept on their own lines | B | 6 |
| Tokens per answer, and estimated cost per college | B, C | 6, 7 |
| Consent and age not asked twice in one chat | C | 6 |
| Automated accessibility check and its fixes | C | 6 |
| Degraded mode in the widget | A, C | 7 |
| Staff photo, spoken rating, SMS replies, browser push | B, C | 8 |
| Each messaging step runs on its own | C | 9 |
| Plain catalogue questions answered without the model | B | 9 |
| The gate reuses an unchanged baseline | A, B | 9 |
| No eligibility verdicts about the visitor (roadmap item 1) | B | 10 |
