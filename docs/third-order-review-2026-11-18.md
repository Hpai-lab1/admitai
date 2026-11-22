# Third-order review (18 Nov 2026)

This review looks past whether each part works today. For each area it asks three questions:

- **First order:** does it work correctly now?
- **Second order:** what happens at scale, when an outside service fails, when the college's
  information changes, or when many people use it at once?
- **Third order:** what long-term technical, operational, financial and user-experience
  consequences follow?

It builds on the measured findings of the nine earlier reviews in `docs/` (all 18 Nov 2026) and
two further facts measured for this one:

- **Tokens per answer:** a Gemini answer sends about **5,500 input tokens** and returns about
  **390** (138 answers; `chat.messages`). Hindi and Kannada add a rewrite into English and a
  translation (two more calls of similar size).
- **Website changes go live without review:** the nightly crawl stores pages as approved documents
  and replaces the catalogue for the year it finds (`site_kb.py`, `pack.py:110`). A guard keeps the
  old catalogue if a crawl finds far fewer programmes (`MIN_SHARE`), but a changed fee or date is
  published with no staff check.

It follows the brief's last rule: **nothing here is a new feature for its own sake.** Most
recommendations remove, merge or narrow something.

## 1. The three orders, area by area

### 1.1 Knowledge and its freshness

| Order | Assessment |
|---|---|
| First | The website crawl builds 102 documents and 81 programmes with fees, intake and eligibility; answers cite them. Readiness still fails: fees missing for 9 programmes, dates for ~40, owners for most pages. |
| Second | When RUAS edits its website (a new fee circular, a corrected date, a page redesign), the nightly crawl publishes the change at once, with no person looking at it. A typo on the website becomes the assistant's answer the next morning, cited and checked against itself. When the website is slow or blocked, the crawl keeps yesterday's catalogue (good), but nothing tells staff. |
| Third | The PRD makes the **catalogue** the authority, owned by the admission officer (F4.3, 17.1). Auto-publishing inverts that: the website becomes the authority and staff become observers. When a wrong fee is quoted to parents, the college will ask who approved it, and the honest answer is nobody. Over a year, trust in the assistant will track the quality of the website, which the admissions office does not control. |

**Recommendation:** keep the crawl, but make it **propose** changes: a nightly "what changed on
the website" list in the console (new programme, fee changed from X to Y, page removed), approved
by the owner role, with fees and dates never auto-published. This is the PRD's own L2 item F4.9
and it reuses the existing approval inbox; it replaces silent publishing rather than adding a
feature.

### 1.2 Retrieval

| Order | Assessment |
|---|---|
| First | Hit@1 48%, Hit@4 70%, Hit@8 74% on the 54 in-scope RUAS questions (AI review). |
| Second | The same FAQ exists twice (curated answers and the crawled FAQ page) and the website copy wins 30 of 54 times; 32% of chunks are fragments under 200 characters. As more pages are crawled and more programmes added, fragments and duplicates grow faster than useful text, so retrieval gets **worse with more content**, not better. |
| Third | Staff corrections go into curated answers. If the website copy keeps winning, corrections stop changing what visitors see, staff conclude that correcting is pointless, and the learning loop — the PRD's main differentiator — quietly stops. |

**Recommendation:** remove the duplication (index curated answers, not the FAQ page they came
from), merge fragments into sections, and add question variants to curated answers. Then **cut
the prompt from 8 sources to 4**: Hit@4 (70%) is almost Hit@8 (74%), so the extra four sources
mostly add tokens (the sources are most of the 5,500-token prompt), time and room for the model to cite the wrong one.

### 1.3 The answer model

| Order | Assessment |
|---|---|
| First | Gemini 3.1 Flash-Lite answers English in 1–3 s when Google is fast; the local `qwen2.5:3b` is the fallback. |
| Second | On the free key, response times swing from 1 s to 15 s; after a burst of use the key is throttled and the 3B fallback answers, slowly and poorly. In admission season (the PRD's whole premise is a spike), every college hits the same limits at once. If the key is revoked or the model retired (it happened to 3.5 Flash-Lite's speed during this project), answers degrade overnight. |
| Third | The project now has three model stories: the PRD's lab models (Qwen-14B, Llama-8B, DeepSeek — not connected), the presentation's self-trained 13–15B model (not built), and the running hosted Gemini. A college cannot plan cost, privacy or quality around three stories. |

**Recommendation:** pick one primary path and document it everywhere: **a paid Gemini key under a
data-processing agreement for colleges that opt in, and the lab's 14B model as the default
otherwise**. Make the fallback an honest message plus a callback for Indian languages instead of
a 3B answer. Correct the presentation (no training, no FAISS, no LangGraph).

### 1.4 Hindi and Kannada

| Order | Assessment |
|---|---|
| First | Push-to-talk hears all three languages correctly; real answers 5/10 (Hindi) and 9/16 (Kannada) in the test set; 3–4 model calls per turn, 12–16 s median. |
| Second | Each Indian-language question costs three model calls, so it is three times as exposed to Gemini's slow periods and costs roughly three times as much. The translation check (numbers must match) rightly blocks bad translations, which appears to the visitor as "could not answer reliably" — 6 of 26 Indian-language cases. |
| Third | Students and parents who ask in Kannada get a slower, less complete service than those who ask in English. For a Karnataka university that is the opposite of the inclusion the PRD promises, and the gap will be visible in its own analytics. |

**Recommendation:** translate **approved content once, when it is approved**, not every answer at
request time: curated answers and catalogue facts stored in English, Hindi and Kannada, checked by
the existing `same_facts`, and edited by staff like any other answer. A curated hit then needs one
model call or none. This also gives staff something they can review — they cannot review a
translation made live for one visitor.

### 1.5 Voice

| Order | Assessment |
|---|---|
| First | Push-to-talk works in three languages; Retell calls work in English but half of 74 calls failed on connections; the one telephone call failed; WhatsApp voice notes and LiveKit never ran. |
| Second | Five voice paths (Retell web, push-to-talk with Sarvam, Vapi phone, LiveKit, WhatsApp notes) each have their own provider, keys, failure modes, latency and privacy terms. Every provider outage or API change (Retell retired two endpoints during this project) is a separate incident. |
| Third | A small team will spend its time keeping integrations alive instead of improving answers. Each provider also processes student voices outside India, which multiplies the agreements a college must sign. |

**Recommendation: narrow, do not add.** For the pilot keep **push-to-talk** (working, testable,
one provider, gated per college) and **one** telephone path once it completes a real call. Park
LiveKit (built, never run) and treat Retell as optional per college. Do not add Bolna or any other
provider (the README already reached this conclusion).

### 1.6 Staff productivity

| Order | Assessment |
|---|---|
| First | The console works and every action reaches the backend; permissions are correct. |
| Second | Staff cannot fix the knowledge themselves (read-only Knowledge page); the approval inbox fills with non-questions; there is no password reset. In season, with real volume, the inbox grows faster than staff can clear it, and every missing fee needs a developer. |
| Third | If the assistant creates work instead of removing it, the admissions office will route around it — answer the phone as before and stop opening the console. The PRD's measure of success (resolution rate, staff time saved) then cannot be achieved, whatever the model quality. |

**Recommendation:** spend the next development effort on **three staff screens**, not on new AI:
an editable Programmes tab (fees, dates, eligibility), the website-changes list (1.1), and an
approval inbox that filters non-questions and opens with a draft already written. Measure
"minutes of staff time per week" from the audit log.

### 1.7 The self-learning layer

| Order | Assessment |
|---|---|
| First | Built carefully (evidence from the college's own pages, three trusted sessions, gate, automatic rollback); tested. |
| Second | At pilot volume it will rarely act: RUAS has 2 feedback rows and 35 conversations in 30 days, and a lesson needs three distinct trusted sessions agreeing. Meanwhile it adds a worker, tables and a second approval path to reason about. |
| Third | A complex mechanism that seldom fires is hard to trust and hard to debug when it finally does — usually in peak season, when nobody has time. |

**Recommendation:** keep it **off** for the pilot (its code default already is) and turn it on
when a college reaches a volume where three agreeing sessions are common. Until then the simpler
loop (rating → review → correction → gate) is enough and is what staff can see.

### 1.8 Privacy and consent

| Order | Assessment |
|---|---|
| First | Strong isolation and consent records; but voice providers get raw data without opt-in, masking misses OTPs and names, retention never runs, super-admin sees comments. |
| Second | With real applicants (many under 18), each unmasked OTP sent to Gemini, each kept recording at a provider and each conversation kept past its retention period is a reportable lapse under DPDP. A single parent complaint triggers a review of everything at once. |
| Third | A university's legal office decides adoption. A system that cannot show where student data goes, for how long, under which agreement, will not pass that review regardless of answer quality. |

**Recommendation:** before any real applicant, one page per college (generated from the pack) that
lists every outside service, what it receives, where, for how long, and under which agreement —
and the switches to turn each off. Make it the condition for enabling the service.

### 1.9 Reliability and operations

| Order | Assessment |
|---|---|
| First | Everything runs on one laptop; a quick tunnel gives voice a public address. |
| Second | Sleep, a lid closed, a reboot (twice during this review) or a network change stops the service; the tunnel address changes and Retell and Vapi lose it (12 failed calls). Call registries and some rate limits live in memory. No alerts. |
| Third | A pilot judged on availability will be judged on the laptop. The PRD's lab servers and its 10.3 prerequisites (firewall, backups, access control) are the real path, and they are not in this repository yet. |

**Recommendation:** move the pilot to the lab server with a named tunnel, the 10.3 prerequisites,
nightly encrypted backups with a tested restore, and one alert (e-mail on error rate and on a
failed nightly crawl). Store call registries and rate limits in the database (no new
infrastructure).

### 1.10 Maintainability and quality assurance

| Order | Assessment |
|---|---|
| First | 866 tests pass; type check and lint clean. |
| Second | CI runs none of the database tests and none of the web checks; the engine's own database tests cannot run on the team's 8 GB laptops; contracts cover 17 of 167 routes; the engine's turn function is ~650 lines. A change that breaks the engine can be merged with a green CI. |
| Third | As three students hand the project on, the unwritten knowledge goes with them. The next team inherits a large, partly unverified system and will either freeze it or rewrite it. |

**Recommendation:** database and web checks in CI, the engine test file split so it runs on a
laptop, and the RUAS evaluation as one command whose dated report is kept in the repository. These
are the cheapest ways to make the system survive a hand-over.

### 1.11 Operating cost

| Order | Assessment |
|---|---|
| First | Free tiers today (Gemini free key; trial credits on voice providers). |
| Second | Per answer about 5,900 Gemini tokens in English and about three times that in Hindi or Kannada; per voice minute, separate charges from Retell (plus its speech and voice vendors), Sarvam and Vapi; backup requests (hedging) add duplicates when Google is slow. In season these multiply by every conversation, at the moment the free tier stops being enough. |
| Third | A college cannot budget an unknown per-minute, per-token bill across five vendors, and the PRD's promise of "low cost by default (local models)" no longer holds. |

**Recommendation:** record provider usage per turn and per call (tokens, minutes) and show a
monthly estimate per college on the platform page (PRD F9.4), using prices the college has agreed.
Then reduce the drivers already identified: 4 sources instead of 8, pre-translated content,
catalogue answers without a model, and no hedged duplicates on the paid key unless latency needs
them.

### 1.12 University adoption

| Order | Assessment |
|---|---|
| First | A convincing demo: a real university's programmes, cited answers, three languages, a staff console. |
| Second | A pilot will be judged on numbers the system cannot show yet: accuracy (50% vs ≥ 90%), latency (p95 23.9 s vs ≤ 5 s), resolution rate, and a privacy review. |
| Third | Adoption depends on the admissions office owning the knowledge and the IT/legal office signing off the data flows. Both are workflow and governance questions, not model questions. |

**Recommendation:** define the pilot's success with the college before it starts, using the PRD's
own metric definitions (3.5): accuracy on the college's test set, first-sentence latency, share of
conversations resolved, staff minutes per week, and zero personal-data incidents. Report them
weekly from data the system already logs.

## 2. Design decisions challenged

| Decision | Challenge | Recommendation |
|---|---|---|
| Website crawl publishes directly | Inverts the PRD's ownership: the catalogue should be authoritative and staff-owned | Crawl proposes, owner approves; fees and dates never auto-published |
| Curated answers **and** the FAQ page they came from are both indexed | Duplicates compete; corrections lose | Index one copy |
| Every answer translated at request time | Three model calls per Indian-language turn; unreviewable translations | Translate approved content once |
| Eight sources in every prompt | Hit@4 ≈ Hit@8; doubles tokens and latency | Four sources |
| Five voice paths | Integration upkeep and privacy exposure exceed their value in a pilot | Push-to-talk + one phone path; park LiveKit; Retell optional |
| Free-tier Gemini as the main model | Throttling in season; no agreement | Paid key under an agreement, opt-in; lab 14B default |
| Self-learning on locally | Rarely acts at pilot volume; adds a second approval path | Off until volume justifies it |
| New staff roles (counsellor, finance, hostel warden, management) | Roles without their own queues or screens (finance and hostel see empty lists) | Keep only roles with a real workflow; add the PRD's role table entry for each kept role |
| Presentation describes training a 13–15B model with FAISS / LangGraph | Not what was built; an examiner will ask | Present what exists: checked RAG over the college's own data, no training |

## 3. What not to do

These would make the project look more advanced without improving it, and some would make it
worse:

- **Do not fine-tune or train a model.** Retrieval and data quality are the measured bottlenecks;
  the PRD rules out training in Level 1.
- **Do not replace pgvector with FAISS, Chroma or a hosted vector database.** Retrieval quality is
  limited by duplicates and fragments, not by the index.
- **Do not add LangGraph or an agent framework.** The explicit pipeline is easier to check and test.
- **Do not add another voice or call provider** (Bolna or others).
- **Do not add Redis or a message broker** for the in-memory state; PostgreSQL already does the
  job (Module C's outbox and rate limits).
- **Do not add more languages** until Hindi and Kannada reach the accuracy target.

## 4. Recommended sequence

| When | Work | Measured by |
|---|---|---|
| Before the next review (days) | Rotate the pasted keys; fix masking; super-admin aggregates; correct the presentation; refresh the RUAS test set and run it as one command | test-set report; masking tests |
| Before any real applicant (2–3 weeks) | Per-college opt-in for every outside service and the data-flow page; retention scheduled; password reset; editable Programmes tab; crawl proposes changes; remove FAQ duplication, merge fragments, 4 sources; CI with database and web checks | retrieval Hit@1 ≥ 75%; readiness passes; CI runs all database tests |
| During the pilot | Pre-translated content; one phone path working; move to the lab server with a named tunnel, backups and an alert; provider usage and cost per college; pilot success metrics weekly | Hindi/Kannada median ≤ 5 s; p95 ≤ 5 s; accuracy ≥ 90%; zero data incidents |
| After the pilot | Self-learning on when volume allows; Level 2 items (campaigns, CRM) only if the pilot metrics are met | — |
