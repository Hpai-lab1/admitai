# Product review: does each feature earn its place?

Every feature in AdmitAI, judged as a product someone has to run for real colleges, not as a
list of things built. It complements the [gap analysis](prd-gap-analysis.md) (what the PRD asks
for) and the [roadmap](third-order-improvements.md) (what to do next). 9 Nov 2026.

## The seven questions

1. **Problem:** does it solve a genuine problem for an applicant, a parent, staff or the college?
2. **Intuitive:** does someone understand it without being told?
3. **Reliable:** does it behave correctly when things go wrong?
4. **Maintainable:** can the next developer change it safely?
5. **Fits:** does it work with the rest of the system, without surprises?
6. **Value:** is the value worth what it costs to keep?
7. **Grows:** will it stay useful, and keep working, as colleges and traffic grow?

✅ yes · ⚠️ partly, see the note · ❌ no

## Verdicts in short

- **Core features earn their place:** checked answers, catalogue answers, callbacks, consent and
  the guardian flow, the learning loop and the dashboards.
- **Three features misled people, and were fixed today:**
  - SMS was offered with no provider set up;
  - e-mail said "e-mailed" with no mail server;
  - browser push offered a switch that could not work.
- **Two features are small and optional:** the staff photo and the spoken rating. They stay, but
  nothing else should be built on them.
- **Four things will not hold up as the system grows,** and are on the roadmap:
  - the shared visitor count behind the gate's waiting;
  - copied infrastructure code;
  - a 900-line widget file;
  - polling for notifications.

## For applicants and parents

| Feature | 1 | 2 | 3 | 4 | 5 | 6 | 7 | Verdict and note |
|---|---|---|---|---|---|---|---|---|
| Answers with a source for every fact, checked sentence by sentence | ✅ | ✅ | ✅ | ⚠️ | ✅ | ✅ | ✅ | **Keep.** The core of the product. `checks.py` is large and subtle; every change needs its tests |
| Catalogue questions answered without the model | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | **Keep.** Fee answers in 0.6 s instead of 9-12 s, exact by construction |
| "I don't know" plus the offer of a person, instead of a guess | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | **Keep.** The right failure for admissions |
| No eligibility verdicts about the visitor | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | **Keep.** Protects under-18s from a machine saying "you are not eligible" |
| Callback request and the "connecting you to…" banner | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ⚠️ | **Keep.** The banner polls every 15 s; fine now, reconsider at thousands of open chats |
| Consent, and the under-18 guardian flow | ✅ | ⚠️ | ✅ | ✅ | ✅ | ✅ | ✅ | **Keep.** Legally required. Several steps for minors; consent and age are no longer asked twice in one chat |
| Degraded mode (approved answers when the assistant is down) | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | **Keep.** Small and useful exactly when needed |
| Hindi and Kannada | ✅ | ⚠️ | ✅ | ✅ | ✅ | ✅ | ✅ | **Keep, but be honest:** answers are still in English with a note on the laptop model, and the wording needs native review (roadmap 7, 19) |
| Voice (push to talk) | ✅ | ⚠️ | ✅ | ⚠️ | ✅ | ⚠️ | ⚠️ | **Keep, don't extend yet.** About 6 s per answer on a laptop, English voice only. Hands-free voice waits for the lab (roadmap 11) |
| WhatsApp | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | **Keep.** Outside 24 hours it needs Meta-approved templates (roadmap 13) |
| Ratings, 👍/👎 and reasons | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | **Keep.** They feed the learning loop |
| Spoken end-of-chat rating | ⚠️ | ✅ | ✅ | ✅ | ✅ | ⚠️ | ✅ | **Keep, small.** Few visitors rate by voice. The hint was shown to everyone; **fixed:** now only to visitors who used voice |

## For admissions staff

| Feature | 1 | 2 | 3 | 4 | 5 | 6 | 7 | Verdict and note |
|---|---|---|---|---|---|---|---|---|
| "Needs you today" console home | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | **Keep.** Answers the first question of every shift |
| Callback queue with transcript, AI draft and sources | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | **Keep** |
| Reply by e-mail | ✅ | ⚠️ | ⚠️ | ✅ | ✅ | ✅ | ✅ | **Fixed today.** With no mail server it said "e-mailed" while the message only waited. It now says it is queued and has not gone, and suggests phoning if urgent |
| Reply by SMS | ⚠️ | ❌ | ❌ | ✅ | ⚠️ | ⚠️ | ✅ | **Fixed today.** It was offered with no provider, so staff believed an applicant had been texted. It is now offered only when a provider is set up |
| Reply on WhatsApp | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | **Keep.** A refusal is shown on the task |
| Browser push | ✅ | ⚠️ | ✅ | ✅ | ✅ | ✅ | ✅ | **Fixed today.** The switch appeared without a push key and failed only when pressed. It now says it is not set up. The push carries no applicant data |
| Staff photo on the banner | ⚠️ | ✅ | ✅ | ✅ | ✅ | ⚠️ | ✅ | **Keep, optional.** A personal touch, not a need; staff choose to add one |
| Draft a missing answer from documents | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | **Keep.** It saves writing from nothing, and refuses rather than invents. Watch that staff still check drafts |
| Approval inbox, delegates, second reviews, peak season | ✅ | ⚠️ | ✅ | ⚠️ | ✅ | ✅ | ✅ | **Keep.** PRD 8.3. The inbox has many options; train staff on it in the pilot |
| Keyboard shortcuts in the queue | ✅ | ⚠️ | ✅ | ✅ | ✅ | ⚠️ | ✅ | **Keep.** Useful to heavy users, invisible to others; listed on the page |

## For the college and the platform

| Feature | 1 | 2 | 3 | 4 | 5 | 6 | 7 | Verdict and note |
|---|---|---|---|---|---|---|---|---|
| Learning loop: feedback, proposal, approval, evaluation gate | ✅ | ⚠️ | ✅ | ⚠️ | ✅ | ✅ | ⚠️ | **Keep.** The product's main promise. The gate takes 7-17 min on a laptop; baseline reuse halves it. It is the most complex part of the system |
| Gate waits while visitors are answered | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ⚠️ | **Keep, and fix before scaling:** the count of visitors being answered lives in one Module B process. With several processes the gate sees only one of them. Store the count where all processes see it |
| Onboarding a college from a zip, with a readiness check and launch test | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | **Keep.** A new college in about 3 minutes, with no code |
| Dashboards: quality, topics, channels, model usage | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | **Keep.** They say "unavailable" instead of showing zeros |
| Estimated cost per college | ⚠️ | ✅ | ✅ | ✅ | ✅ | ⚠️ | ✅ | **Keep, off until a real price exists.** A guessed price would mislead; it shows nothing until one is set (roadmap 12) |
| Rate limits, failed sign-in limits | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | **Keep.** Shared by every process |
| Connection pools | ✅ | — | ✅ | ⚠️ | ✅ | ✅ | ✅ | **Keep.** Written three times; see "shared code" below |
| Messaging worker, each step on its own | ✅ | — | ✅ | ✅ | ✅ | ✅ | ✅ | **Keep.** One failing channel no longer stops the others |
| Demo backups | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | — | **Keep** for the demo; production backups are roadmap 9 |

## What will not hold up as the system grows

| Concern | Why it matters | What to do | Roadmap |
|---|---|---|---|
| The gate's view of who is being answered is per process | With several Module B processes, the gate can test while visitors wait | Count active answers in the database or Redis | New: add as P1 |
| Infrastructure code copied into three modules | A fix made in one copy is missed in the others | One small shared package | 10 |
| `Widget.tsx` is about 900 lines | Every widget change risks breaking another part of it | Split it into the conversation, voice, rating and contact parts, without changing behaviour | New: add as P1 |
| Polling for the banner and notifications | Fine at Level 1; costly at thousands of open chats | Server-sent events, only when measurements show the cost | Later |
| Many options in the approval inbox | Staff may miss what matters | Watch how pilot staff use it before adding more | — |

## Built but superficial? Checked

- **None was removed.** Each feature solves a real problem for someone, or the PRD requires it.
- **The staff photo and the spoken rating are the weakest.** They are small and optional, and
  nothing else should be built on them.
- **SMS and the cost estimate were only harmful while they claimed more than they did.** Both now
  appear only when the server can actually deliver.
