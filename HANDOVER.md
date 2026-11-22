# AdmitAI local prototype: handover

All three modules of the AdmitAI PRD (v2.0, Level 1) built together on one laptop in a week, to
show the whole system working end to end before the 13-week team build. This copy has no git
remote and pushing is disabled; see [MIGRATION.md](MIGRATION.md) for how the work could move into
the team repository, module by module, through each owner's pull requests.

**Status at handover (27 Oct 2026):** `scripts/check_all.sh` passes from a fresh bootstrap. That
covers unit tests, lint and typecheck for all three modules and the web app, the contract check,
the walking skeleton, the channels, a zip-to-live onboarding of a new college, the PRD 20 suites,
live answer quality for three colleges, and the full improvement cycle.

## Run it

```bash
source local.env.sh                       # once: creates the git-ignored local.secrets.sh
python scripts/bootstrap_local.py --fresh # databases, colleges, staff, knowledge, agent settings
scripts/run_local.sh                      # A :8100, B :8150, C :8200, web :8210
scripts/check_all.sh                      # everything (about 20 minutes; --quick skips the learning cycle)
```

Prerequisites, staff sign-ins and the tenants are listed in [LOCAL.md](LOCAL.md).

## A 10-minute demo

1. **Widget**: open http://127.0.0.1:8210/demo-site. Ask "What is the fee for B.Tech CSE?" (an
   exact catalogue fee with a citation chip), then "and what about its hostel fee?" (the program
   carries over), then "Is there a swimming pool?" (an honest "I don't know" with a human offered).
2. **Hindi and safety**: ask "हॉस्टल की फीस कितनी है?" (an English answer with a Hindi note on the
   3B model), and "Ignore all previous instructions…" (refused).
3. **Talk to a person**: press the button, agree to be contacted, leave a number. Sign in to
   http://127.0.0.1:8210/console as `officer@example.com` (college `demo-college`): the callback is
   in the queue with the transcript, the AI's draft and its sources.
4. **Minor**: in a new chat say "I am 16 and in class 11"; the contact form goes straight to a
   parent's code; the student's own number is never asked for.
5. **Learning loop**: run `python scripts/e2e_learning.py --rollback`, or by hand: correct an answer
   in the console, run `scripts/nightly_local.sh`, approve the proposal in *Approvals*, watch it
   pass the test set on a sandbox copy and go live; *Recent decisions* shows the gate's numbers.
6. **Voice**: press the microphone in the widget (Chrome), ask a question, press again. Local
   Whisper and Piper; no audio leaves the machine.
7. **WhatsApp**: `python scripts/e2e_channels.py` sends signed Meta-style webhooks; replies are in
   `chat.wa_outbox` (no Meta account is needed locally).
8. **A new college with no code**: sign in as `platform@example.com` (Super-Admin, MFA), open
   *Colleges*, upload `packs/riverside` as a zip (or run `python scripts/onboard_college.py
   packs/riverside`). Readiness, preview and launch; live in about a minute.

## How it fits together

```
 visitor ── widget / WhatsApp / voice ──▶ Module B (assistant, :8150)
                                           guardrail → rules-first router → retrieval → 1 answer call
                                           → every sentence checked → stream; lead + minor rules
                                             │                      ▲
                       search, catalogue     ▼                      │ handoffs, consents, leads,
                                    Module A (knowledge, :8100)     │ channel sessions
                                     catalogue, curated Q&A,        │
                                     documents, hybrid search   Module C (experience, :8200; web :8210)
                                     learning loop + eval gate ◀── feedback / correction events
                                     Tenant Pack import           identity, RLS, consent, leads,
                                                                   queue, approvals, analytics,
                                                                   platform onboarding
```

One PostgreSQL database, one schema per module (`kb`/`learn`, `chat`, `app`), each with
row-level security on every table and its own restricted login role. Modules talk only over HTTP.

## Measured results

| What | Result | PRD target |
|---|---|---|
| Wrong numbers injected into answers | 50 of 50 blocked | 100% blocked (20) |
| Demo college test set (25 questions) | accuracy 100%, refusals 100%, supported claims 74% of 27 | ≥ 90% on real held-out questions |
| RUAS test set (72 questions incl. 16 Kannada, 10 Hindi) | accuracy 76–80%, refusals 100%, supported claims 69–80% of ~47 | ≥ 90% |
| Riverside, onboarded from its pack (20 questions) | accuracy 87%, refusals 100%, supported claims 71–79% of 14 | launch threshold set in its pack: 85% |
| Hindi / Kannada retrieval (right FAQ in top 4) | 10/10 and 15/16 with bge-m3 (1/10 and 3/16 with nomic alone) | Hindi in L1 |
| Routing (20 labelled) / sensitive topics to a human | 18/20 / 7/7 | ≥ 90% / 100% |
| Two-part questions | 12/12 answered or correctly handed off | ≥ 85% |
| Grounding review on real model, 5 test claims | 5/5 judged correctly | ≥ 95% groundedness |
| New college, zip to live, no code | 44–59 s | ≤ 5 working days (G4) |
| First checked sentence, one visitor | about 1.5–4 s | ≤ 5 s p95 |
| First checked sentence, 8 at once | p95 14–18 s on a laptop CPU | ≤ 5 s p95 for 20–40 on the lab GPU |
| Voice, end of speech to audio | about 5.9 s on a laptop CPU | ≤ 3.5 s p95 on the lab GPU |

Everything ran on a MacBook with `qwen2.5:3b`; the PRD's Qwen2.5-14B on the lab GPUs is a
configuration change. Accuracy numbers are from the deterministic grader (expected citations,
facts, refusals); supported claims are from Module B's grounding review on the 3B model, a lower
bound (until Day 9 this column said "groundedness 100%", which only meant no forbidden source was cited); the PRD's DeepSeek-distill judge needs the lab GPUs.

## PRD traceability (Appendix C, Level 1)

✅ built and tested · 🟡 partly built (what is missing is said) · ⬜ not built in this prototype.
"C (existing)" means Module C's work before this week, now wired to the real Modules A and B.

| Requirement | Status | Where / notes |
|---|---|---|
| F1.1 widget, F1.7 human button, F1.8 rating | ✅ C (existing) | Next.js widget + `embed.js`; the PRD's < 60 kB Preact web component is not done |
| F1.2 sentence-buffered streaming, F1.4/F1.4a grounding and checks | ✅ B | `engine.py`, `checks.py`: numbers, citations with word support, promises, relevance |
| F1.3 multi-turn context | ✅ B | last turns in the prompt; follow-ups searched with the previous question |
| F1.4b asynchronous grounding review | ✅ B → A | `review.py`; unsupported answers become proposals |
| F1.5 starter chips, F1.6 language | 🟡 B, C | English + Hindi detection and UI; Hindi *answers* off on the 3B model (`NATIVE_ANSWER_LANGUAGES`) |
| F1.11–F1.12 WhatsApp | 🟡 B | webhook, formatting, consent-first callbacks, reactions; tested with signed fake payloads; templates outside the 24-hour window and Meta verification not done |
| F2.1–F2.6 voice | 🟡 B, C | push-to-talk over HTTP, local Whisper + Piper, voice style, captions, barge-in, 5-session cap; no LiveKit/WebRTC streaming or hands-free VAD |
| F2.9 spoken lead capture | ✅ B | "call me" on voice → number read back digit by digit (English/Hindi words, Devanagari digits, "double"), saved as consent + lead in C only after "yes"; never in the transcript, dropped after 10 min; no own number for under-18s |
| F2.10 voice eval switch | ⬜ | voice uses the same engine, curated answers and catalogue as chat |
| F3.1, F3.1a, F3.3 routing, multi-intent, handoff package | ✅ B | rules first, 8B-class model only when inconclusive; failing part handed off alone |
| F3.2, F3.4, F3.6 queues, callbacks, staff replies | ✅ C (existing) + B | package now built from B's record (with AI draft, sources, trace) |
| F4.1–F4.8 knowledge base | 🟡 A | upload (PDF, DOCX, PPTX, XLSX, CSV, HTML, TXT, MD), crawl, catalogue, curated Q&A, approval, expiry, versions, rollback, hybrid search with a cross-encoder reranker; no OCR (scanned PDFs flagged), no pgvector |
| F5.1, F5.4, F5.6 capture, scoring, UTM | ✅ B, C | lead prompt after two answers; UTM on the session |
| F5.2, F5.3, F5.5 consent, guardian verification, lead list | ✅ C (existing) + B | minor detection in B; 0 student contact stored without guardian (suite) |
| F6.7 consent and UTM foundations | ✅ C (existing) | |
| F7.1–F7.5 staff console | ✅ C (existing) | |
| F7.7 approval inbox | ✅ C + A | impact order, batch approval, old vs new side by side, gate results, rollback |
| F8.1–F8.4 feedback and corrections | ✅ C + B + A | web and WhatsApp; corrections version existing answers |
| F8.5, 8.x improvement loop | ✅ A | correctness-first triage, clusters, impact, SLA + escalation, sandbox gate, rollback, weekly report |
| F9.1–F9.3 analytics | ✅ C + B | PRD 3.5 metrics now include WhatsApp conversations from B; answer outcomes and first-sentence p95 by channel; an outage of B is shown, not counted as zero |
| F9.4 platform ops (GPU, cost) | ⬜ | latency is in `chat.traces`; no GPU dashboard |
| F10.1–F10.3, F10.6 Tenant Pack, readiness, launch test | ✅ A + C | whole-pack validation; zip-slip and duplicate checks |
| F10.4 sandbox preview | ✅ C | onboarding colleges open only for their own staff |
| 7.1–7.5 agents, latency budget | 🟡 B | same steps as the PRD, as an explicit pipeline rather than LangGraph; ≤ 2 hot-path model calls |
| 9.4 RAG pipeline | ✅ A | hybrid vector + full-text with RRF, then bge-reranker-base (int8 ONNX, CPU) over the best 8 with title and section; English only (Indic questions keep hybrid order) |
| 9.5 model gateway | ✅ B (L1) | any OpenAI-compatible endpoint (LiteLLM-ready by URL); each college's `model_policy` is enforced: primary → fallback, voice on the 8B model, output cap; only allow-listed local models (`MODEL_ALIASES`), `external` must be `none`, checked before onboarding loads anything. L2 external-model rules (PII redaction, usage log) not built |
| 5.4, 11.3 identity, RLS, audit | ✅ C (existing) + A, B | RLS on every table in all three schemas, tested per module |
| 9.1 least privilege between services | ✅ A, B, C | a key per caller and callee with per-endpoint allowlists; RS256 visitor tokens that B can verify but not mint; production refuses dev settings at start-up |
| 11.4 retention, NFR erasure on request | ✅ C → B → A | widget, WhatsApp and admin erasure through C's outbox (retried until delivered); nightly retention in B and C; PII masked at A's signal intake |
| 12.2 outbound webhooks | ✅ C (+ A relay) | lead.created/updated, handoff.created/resolved, improvement.deployed, kb.sync.completed; HMAC-SHA256 signed, 24 h backoff, event ids; SSRF-safe URLs; erasure cancels queued data and tells the CRM to forget. conversation.closed is not sent (B does not close conversations) |
| 12.3 API standards | 🟡 A, B, C | RFC 9457 problem+json with a trace id everywhere; 429 + Retry-After per IP and per tenant; OpenAPI generated. `/v1` path prefix left to the guide (contract change; a proxy can add it) |
| 17 content operations | ✅ A | owners, catalogue wins, 90-day unused, cut-over, health report |
| 20 evaluation | 🟡 A + B | test sets, deterministic grader, gate, suites; groundedness is now measured claim by claim with B's grounding review and gates every change (an unmeasured run fails the gate); DeepSeek judge and model comparison need the lab GPUs |
| 10, 18 deployment, pilot operations | ⬜ guide | local scripts only; degraded-mode *export* exists, the edge page does not |

## Deviations from the lab set-up (all configuration-level)

| PRD (lab) | Here |
|---|---|
| Qwen2.5-14B answers, Llama-3.1-8B routes, via LiteLLM | `qwen2.5:3b` for both through Ollama's OpenAI-compatible API |
| nomic-embed-text | nomic for English plus bge-m3 for Indic scripts (the PRD lists bge-m3 for Level 2) |
| pgvector HNSW | `real[]` vectors searched in Module A |
| Self-hosted Supabase, Langfuse, Redis | Module C's JWT auth, `chat.traces`, in-process rate limits |
| LiveKit voice | push-to-talk over HTTP |
| Docker Compose, Cloudflare Tunnel | `scripts/run_local.sh` on 127.0.0.1 |

## Known limitations

- Hindi and Kannada answers on a 3B model are poor; the checks block what goes wrong, so many
  become "not certain". Switch on `NATIVE_ANSWER_LANGUAGES=en,hi` with the 14B model.
- The deterministic checks cannot catch every unsupported claim without numbers; the asynchronous
  review catches them later. A wrong answer can therefore be shown before the nightly review.
- Groundedness is measured with a 3B judge and is a lower bound: it misses paraphrases ("two
  years" for "24 months"). Re-measure with the lab's larger judge before comparing with the PRD's 95%.
- Test sets are synthetic or come from the RUAS prototype (72 questions); the PRD requires 150–500
  real questions per college before a real launch.
- Laptop latency under load is far from the PRD target; it must be measured on the lab GPU.
- The Kannada notes were written without a native speaker. The Piper voice's licence must be
  checked before commercial use.
- RUAS content is the prototype's FAQ set, including SAMPLE placeholders (loaded as drafts); it
  must not be used with real applicants and must not go into the public repository.
