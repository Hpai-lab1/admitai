# AdmitAI: final evaluation (Level 1)

9 Nov 2026. This is the closing assessment of the review. It draws on the
[PRD gap analysis](prd-gap-analysis.md), the [improvement roadmap](third-order-improvements.md)
and the [product review](product-review.md). Every number below comes from a recorded test run
or from the code. Where something has not been measured, this document says so.

**In one paragraph:**

- **Ready for a demonstration and a supervised pilot,** where staff watch every conversation.
- **Not ready to run without supervision,** for three reasons:
  - on real questions it answers correctly only 74% of the time, against a 90% target;
  - about 1 in 7 of its factual claims are not confirmed by their source, against a target of
    1 in 20;
  - its speed has only been measured on a laptop, where it misses the target by a wide margin.
- **The engineering underneath is sound:** isolation between colleges, consent, refusing rather
  than guessing, and recovery from failures. What is missing is evidence on the lab servers and
  on real colleges' questions.

## 1. PRD compliance

### Requirements (Level 1)

Of the 53 Level 1 requirements traced in the gap analysis:

| Status | Count | Share |
|---|---|---|
| ✅ Implemented, with a passing test or a live run | 35 | 66% |
| 🟡 Partial | 14 | 26% |
| ❓ Needs verification (lab only) | 3 | 6% |
| ⬜ Missing (deployment and pilot operations, owned by the guide) | 1 | 2% |
| ❌ Non-compliant | 0 | 0% |

Every partial item is partial because of something outside the code:
- the lab servers (voice worker, GPU latency, judge models, Hindi on the larger model);
- an outside party (Meta templates, an SMS provider);
- a decision by the guide (contracts, `/v1`, the Preact widget).

No requirement is partial because the code is incomplete where it could have been finished.

### Quality and performance targets (PRD 16 and 20)

The requirements say what the system does. These targets say how well, and this is where Level 1
falls short.

| Target (Level 1) | Required | Measured | Verdict |
|---|---|---|---|
| Factual accuracy on the college's test set | ≥ 90% | Demo college 93%, Riverside 97%; **RUAS (real questions) 74%** | ✅ on synthetic sets, **❌ on the only real set** |
| Groundedness (claims supported by their source) | ≥ 95% | Demo college **83–86%**, Riverside **90%**, RUAS 95% | **❌** on two of three sets |
| Sentence check blocks injected wrong numbers and missing citations | 100% | 100% (every swap of real catalogue values blocked, in tests) | ✅ |
| Honest "I don't know" on unanswerable questions | ≥ 90% | 100% on every set | ✅ |
| Routing to the right team | ≥ 90%; 100% of sensitive | 97–100%; sensitive always handed off | ✅, but on 20–60 questions, not the 200 required |
| Same facts on widget and WhatsApp | Same facts | 3 of 3 (100 required) | ✅ on a small set |
| Minors: no student contact data without guardian consent | 0 stored | 0 | ✅ |
| First checked sentence, chat | ≤ 5 s p95 | Laptop, 8 conversations at once: **p95 16.1 s**. Catalogue questions: **0.6 s** | **❌ on a laptop**; lab not measured |
| Voice, end of speech to first audio | ≤ 3.5 s p95 | Laptop CPU: **10.1 s** | **❌ on a laptop**; lab not measured |
| Concurrency | 20–40 chats, 5 voice | 8 chats tested, no errors | ❓ not tested at the target |
| Security: OWASP ASVS L1, TLS, MFA, RLS | All | MFA ✅, RLS ✅ (tested, including a check that every table has it), secrets out of code ✅; ASVS not assessed; TLS is the lab's | 🟡 |

**How far to trust these numbers:**

- **Groundedness is judged by the same small model that writes the answers.** The 83–86% may
  understate or overstate the truth. The PRD's DeepSeek judge, on the lab GPUs, is the real
  measure.
- **Accuracy on synthetic sets says little.** The 93% is on questions the team wrote. The 74% is
  on real questions, and that is the figure that predicts the pilot.

## 2. Architectural weaknesses that remain

Strengths first, so the list is fair: isolation between colleges by Row-Level Security (tested
for every table); a deterministic sentence check rather than a model judging itself; consent
enforced by the database; an evaluation gate before any change goes live; and graceful failure
when any one module is down (23 of 23 checks).

| # | Weakness | Consequence | Roadmap |
|---|---|---|---|
| W1 | **Answer quality rests on a 3B model on a laptop**, and nothing has been measured on the 14B lab model the design assumes | Every quality and speed figure above may change, for better or worse, on the real hardware | 7, 12 |
| W2 | **The grounding review uses the same small model** that wrote the answer | It can miss its own mistakes, or flag correct claims, so groundedness is not yet a trustworthy metric | PRD 20 judge (lab) |
| W3 | **Test sets are small and mostly synthetic** (20–72 questions; 150 real ones required) | The gate can pass a change that hurts real questions, or block a good one; one question flipping moves accuracy by more than a point | 5 |
| W4 | **State still kept inside one process:** the gate's count of visitors being answered, and the degraded-mode cache | With more than one process, the gate can test while visitors wait | 20 |
| W5 | **Contracts describe 8 of Module C's 61 endpoints**, and nothing enforces them | Modules drift apart unnoticed; breakage is found in the demo, not in review | 3 |
| W6 | **No API versioning** | The first college's widget breaks on the first incompatible change | 4 |
| W7 | **Infrastructure code copied into three modules** | A fix lands in one copy and not the others | 10 |
| W8 | **Long jobs run inside the web process** | Recovery exists, but with several processes, jobs may run twice or stall | 16 |
| W9 | **Background work is polled** (banner, notifications, worker loop) | Fine now; it costs more as open chats grow | Later |
| W10 | **The widget is one 916-line file** | Each change risks another part of the widget | 21 |
| W12 | **The fact check binds numbers to labels only in English.** Found when Kannada answers were tried: a Kannada answer stated the hostel fee as the B.Tech fee, with a citation | Native Hindi or Kannada answers are unsafe until fixed; they are switched off | 22 |
| W11 | **Process:** about 2,200 lines changed in Modules A and B on 8–9 Nov are in local commits, not yet reviewed by those modules' owners | Merge conflicts, and changes nobody else understands; the team's ownership rule is not yet honoured for them | See section 6 |

## 3. Third-order opportunities identified

Opportunities the PRD does not ask for directly. Those marked **built** are done; those marked
**new** are not yet on the roadmap.

| Opportunity | Why it matters beyond the PRD | Status |
|---|---|---|
| Answer catalogue facts from the catalogue, not the model | The most common questions become instant and exact; less GPU per college | Built (English fees and durations); Hindi, Kannada and dates are roadmap 6 |
| Draft missing answers from the college's own documents | Staff time is the bottleneck of the learning loop | Built |
| Degraded mode inside the widget | An outage still gives applicants approved answers and a person | Built; the edge page is roadmap 9 |
| "Needs you today" for staff | Overdue callbacks lose applicants; one screen prevents that | Built |
| Visitors before tests on the shared model | The gate no longer slows real applicants | Built |
| Never judge an applicant's eligibility | Protects under-18s from a machine turning them away | Built |
| Offer only channels that can deliver | Staff never believe a message went when it did not | Built |
| Cost per college from measured tokens | Pricing based on evidence, as the PRD wants, is now possible | Built; a real price needs the lab (roadmap 12) |
| **Turn real questions into test cases, with consent** | Real questions the assistant failed on, labelled by staff in the approval inbox, become held-out test cases automatically. That is the fastest route to the 150 real questions (W3) | **New:** recommended as P0 together with roadmap 5 |
| **Deadline reminders for consented leads** | Applicants who asked about a programme get one reminder before its last date, by the channel they chose. It serves the PRD's lead goal (G2) with data the system already holds | **New:** P1, needs a consent-wording review |
| **A content-gap report for the college** | Trending unanswered questions show what the college's own website fails to say: a fix at the source, not only in the assistant | **New:** P1; the data is already in the topics dashboard |

## 4. Improvements implemented

Built across gap-analysis passes 3 to 12 (8–9 Nov 2026). Each is in the module READMEs and the
roadmap's "already built" table.

| Area | Improvements |
|---|---|
| **Answer correctness and safety** | Each number tied to its own fact and programme; longest programme name wins; one more try for a missing citation, within the two-call budget; shared fees said once; catalogue answers without the model; no eligibility verdicts, in English, Hindi and Kannada; WhatsApp sources kept apart from the answer |
| **Reliability** | Each messaging step isolated; launches finish without the page open; the dashboard no longer holds a transaction during a Module B call; connection pools; degraded mode; an unchanged baseline reused; one evaluation at a time, waiting for visitors; demo backup and restore |
| **Security** | Failed sign-ins and two-factor codes limited across processes; limit keys stored as HMACs; push restricted to real push services; staff photos type-checked from their bytes; a test that every table has RLS |
| **Staff experience** | "Needs you today"; drafts from documents; handoff banner with name and photo; reply by e-mail, WhatsApp or SMS, offered only when deliverable; browser push without applicant data |
| **Applicant experience** | Consent and age not asked twice; Kannada; spoken rating; accessibility fixes (0 automated WCAG 2.2 AA violations on 7 pages, light and dark) |
| **Operations** | Intents and trending questions; usage per model, with tokens; cost per college when a price is set |

## 5. Recommended for future releases

The full list, with every field, is in the [roadmap](third-order-improvements.md). In order:

| When | Do this | Why first |
|---|---|---|
| **Before any real college (P0)** | Run the test set on the 14B lab model and the DeepSeek judge (W1, W2) | Every other decision depends on whether quality and speed hold on the real hardware |
| | Collect 150 real questions per pilot college, and turn failed real questions into test cases (W3, new opportunity) | The 74% on real questions is the most important number in this document |
| | Have native speakers review the Hindi and Kannada wording, especially consent (roadmap 19) | Consent on misunderstood wording is not informed consent |
| | Agree the contracts, check them in CI, and add `/v1` (W5, W6) | Cheap now, expensive once a college is live |
| | Keyboard and screen-reader check with the test script | PRD 13; automated checks cover about a third |
| **During the pilot (P1)** | Alerts and monitoring; production backups and the edge page; a shared count of active answers (W4); split the widget file (W10); Hindi and Kannada catalogue answers; deadline reminders; the content-gap report | Each serves a goal the pilot measures |
| **When scale calls for it (P2)** | A job queue (W8), signed service tokens, sandbox overlays, server-sent events (W9) | Only after measurements show the need |

## 6. Risks and unresolved dependencies

| Risk | Likelihood | Impact | Mitigation | Owner |
|---|---|---|---|---|
| Quality on real questions stays below 90% on the lab model | Medium | High: G1 missed; applicants misinformed | Real test sets, the lab model, catalogue-first answers; keep the pilot supervised until the target is met | Team, lab |
| The lab hardware misses the 5 s latency target with 20–40 chats | Medium | High: applicants leave | Load test on the lab first; catalogue answers remove the model from the most common questions | Lab, Module B |
| A wrong eligibility or fee answer reaches a minor | Low | High | Sentence checks, the verdict rule, guardian consent; supervised pilot | Module B |
| Translated consent wording is misunderstood | Medium | High (DPDP) | Native review before use (roadmap 19) | Team |
| The modules drift apart without enforced contracts | High | Medium | Contracts plus a CI check (roadmap 3) | Guide |
| Unreviewed changes in teammates' modules (W11) | High | Medium: merge conflicts; ownership rule broken | Hand the Module A and B changes to their owners as separate pull requests, explained; do not push them as Module C | You, teammates |
| Data loss on the server | Low | High | Backups with a tested restore (roadmap 9); demo backups exist | Guide |
| WhatsApp outside 24 hours refused for lack of templates | High until approved | Medium | Meta verification per college (roadmap 13) | Colleges |
| Thin evidence for the thesis claims | Medium | Medium | Report the real-set figures alongside the synthetic ones, as this document does | You |

**Unresolved dependencies:**

- **The lab servers:** the 14B model, the DeepSeek judge, GPU metrics, LiveKit, monitoring.
- **The guide:** contracts, `/v1`, deployment, the CI job.
- **The teammates:** review and merge of their modules' changes, and the shared package.
- **The colleges:** real questions, WhatsApp verification.
- **Outside services:** an SMS provider with DLT registration.
- **People:** native speakers for Hindi and Kannada, and an accessibility tester.

## 7. Testing and verification

**What passed on the final code, 9 Nov 2026:**

| Check | Result |
|---|---|
| Unit and integration tests | Knowledge 109 (4 skipped: OCR and pgvector need system packages); assistant 273; experience 111; web 63. **556 in total** |
| Full demo reset (rebuild, learning cycle, new college) | Passed in 23 min |
| Walking skeleton (end-to-end flows) | 19/19 |
| Channels (web and WhatsApp) | 14/14 |
| PRD suites (isolation, masking, forged webhooks, service-only APIs, channel consistency, load) | 13/13 |
| Learning cycle (feedback, proposal, approval, gate, deploy) | Passed |
| Catalogue answer speed | 0.6 s to the first sentence, from 9–12 s |

**Passed on 8 Nov code, not repeated on the final code:**

| Check | Result | Why not repeated |
|---|---|---|
| Browser rehearsal of all 23 demo steps | 23/23 | About 17 minutes of heavy model load on the laptop; skipped at the user's request |
| Resilience (bad input; each module stopped in turn) | 23/23 | Same |
| Live probe (11 questions × 5) | 9 answered 5/5; refusals and the injection behaved as designed | Same |

**Other measurements:**

- **Accessibility:** axe-core on 7 pages, light and dark: 0 violations, after fixing 6 problems.
- **Widget on 4G:** ready in 2.5 s on slow 4G (282 kB) and 0.9 s on fast 4G; the college page
  loads only 3.4 kB.
- **Backup:** restored completely into a throw-away database.

**Not verified at all:**

- anything on the lab servers;
- 20–40 chats at once;
- a screen reader;
- real WhatsApp delivery outside the sandbox;
- SMS and browser push with real providers;
- Kannada answers from a model;
- the native accuracy of the Hindi and Kannada wording.

**How much the tests prove:** most tests use fake models and fake services, so they prove the
logic and the safety rules, not the quality of the answers. Quality evidence comes only from the
test-set runs above, on a 3B model.

## 8. Overall readiness

| Use | Ready? | Why |
|---|---|---|
| **Thesis and professor's demo** | **Yes** | Every Level 1 flow works end to end, is tested, and degrades safely; the demo is rebuilt and backed up |
| **Supervised pilot with one college** | **Conditionally** | After the P0 items (lab validation, real test questions, native wording review, contracts and `/v1`), and with staff watching every conversation |
| **Unsupervised use by real applicants** | **No** | Accuracy on real questions 74% (90% required), groundedness 83–90% (95% required), latency unmeasured on the target hardware, production backups and monitoring missing, ASVS not assessed |

**Scorecard** (out of 5, for Level 1):

| Area | Score | Comment |
|---|---|---|
| Scope against the PRD | 4.5 | Everything buildable is built; the gaps are dependencies |
| Safety and privacy | 4.5 | Refuses rather than guesses; consent and isolation enforced in the database |
| Reliability | 4 | Degrades well; production operations missing |
| Answer quality | **2.5** | The weakest area, and the one that decides the product's success |
| Performance | **2.5** | Good on catalogue questions; unproven on the target hardware |
| Maintainability | 3.5 | Well tested; copied code, a large widget file, unenforced contracts |
| Evidence | 3 | Strong for logic, thin for quality on real questions |

**The one thing that matters most next:** run the real questions on the lab model. If accuracy
reaches 90% there, the product is close to a pilot. If it does not, the right investment is
catalogue-first answers and better sources, not more features.
