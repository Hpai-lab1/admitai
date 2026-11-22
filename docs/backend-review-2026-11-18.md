# Backend and database review (18 Nov 2026)

Scope: the three FastAPI services (Module A `knowledge_api`, B `assistant_api`, C
`experience_api`), their PostgreSQL schemas (`kb`, `learn`, `chat`, `app`) and how they talk to
each other. Method: code reading, the live database's catalogue (constraints, indexes, row-level
security), endpoint timings on the running system, and the permission run from the console review.

No technology replacement is recommended. FastAPI, Pydantic v2, psycopg 3 with a pool, PostgreSQL
with RLS, and plain HTTP between modules are the right size for a pilot of a few colleges; every
recommendation below works inside them.

## Summary

The backend is in better shape than the frontend findings suggest. Errors are uniform, inputs are
typed, authentication and tenant isolation are enforced in the database itself, connections are
pooled, each module touches only its own schema, and every service refuses to start with
development settings in production. Measured staff endpoints answer in 5–90 ms.

The weak points are about running more than one process, recovering from a module being down, and
a few places where the same logic lives twice.

| # | Finding | Priority |
|---|---|---|
| B1 | State that lives in one process's memory: Retell and Vapi call registries, Module B's visitor rate limits, the live-turn counter | P1 (before more than one worker or a restart during calls) |
| B2 | Module B → Module C calls are synchronous with no retry: a spoken phone number or a WhatsApp opt-in is lost if C is down | P1 |
| B3 | PII masking written twice (A `pii.py`, B `guard.py`), with different patterns; both miss OTPs and names | P0 (privacy) |
| B4 | Background recovery in Module A swallows its errors without logging | P1 |
| B5 | List endpoints are inconsistent: some paginate (`limit`), curated answers (136 KB) and documents (56 KB) return everything; some lists are wrapped in `{"items": …}`, some are bare arrays | P1 |
| B6 | Module A and Module B call each other (search one way, evaluation the other): a start-order and failure coupling | P2 |
| B7 | Module B's `PUT /tenant-settings` takes an untyped `dict` | P2 |
| B8 | No API version prefix (`/v1`), already raised in `third-order-improvements.md` | P1 |

## 1. API design and response consistency

| Aspect | Found | Assessment |
|---|---|---|
| Error format | `application/problem+json` with `type`, `title`, `status`, `detail`, `instance`, `trace_id` in all three services (`problems.py`, identical copies) | ✅ consistent |
| Rate limits | 429 with `Retry-After` | ✅ |
| Trace ids | `X-Trace-Id` accepted from the caller, echoed, and stored with each turn's spans | ✅ one id follows a request across modules |
| Naming | resource-style paths (`/leads/{id}`, `/approvals/{id}/rollback`); a few action paths (`/approvals/decide`, `/reviews/run`) | ✅ acceptable |
| Versioning | none | ⚠️ add `/v1` before a second college depends on it (B8) |
| List shape | mixed: `{"items": [...]}` (documents, curated answers) and bare arrays (corrections) | ⚠️ pick `{"items": [...], "next": cursor}` for every list (B5) |
| Pagination | `limit` on about 12 lists; none on documents and curated answers | ⚠️ (B5); the console's 29,771-px Knowledge page follows from this |
| Contracts | 17 of 167 routes in `contracts/` (Phase 2, T2) | ⚠️ |

## 2. Request validation

- Request bodies are Pydantic v2 models in all three services, with lengths and patterns (for
  example `LoginIn`, `MfaIn` with a 6–8 character code, uuid checks on `X-Tenant-Id`).
- Exception: Module B `PUT /tenant-settings` (`routes/chat.py:308`) takes `config: dict` and checks
  only lead scoring and audio settings (`scoring_problems`, `audio_store.problems`). A malformed
  pack field reaches the engine at the next turn. **Fix:** a `TenantSettings` model (it can allow
  extra fields) validated on PUT, with the same checks Module A's pack loader already applies (B7).
- Vapi and Retell payloads are read as JSON without a model, by design (their schemas change); the
  code only reads the fields it needs. Acceptable.

## 3. Authentication and authorization

| Caller | Mechanism | Assessment |
|---|---|---|
| Staff | scrypt password, constant-time dummy check, rate-limited sign-in and MFA, TOTP for admins, refresh tokens | ✅ (missing: password reset, forced change of temporary passwords — console review) |
| Visitor | RS256 token signed by C, verified by B with the public key only | ✅ B cannot mint visitor tokens |
| Voice | short-lived HS256 voice token (`VOICE_TOKEN_SECRET`) | ✅ |
| Service to service | one key per caller pair (`KEY_C_TO_A`, `KEY_B_TO_A`, …) plus an allow-list of method and path per caller (`auth.ALLOWED`) | ✅ least privilege |
| Meta WhatsApp | HMAC-SHA256 of the raw body, `compare_digest` | ✅ |
| Vapi | `X-Vapi-Secret` or Bearer, `compare_digest`; unknown calls without it get 401 | ✅ |
| Retell LLM WebSocket | accepted only for a call id this service created in the last hour | ✅ for the pilot; the registry is in memory (B1) |
| Authorization | checked on the server for every endpoint tested; RLS underneath | ✅ — one PRD gap: super-admin reads raw feedback (console review R1) |
| Production guard | `check_production` in all three services refuses development keys, echo codes and the MFA bypass | ✅ |

## 4. Database design

| Schema | Tables | Foreign keys | Indexes | RLS on / forced |
|---|---|---|---|---|
| `app` (C) | 25 | 40 | 39 | 24 / 23 |
| `chat` (B) | 13 | 3 | 20 | 12 / 12 |
| `kb` (A) | 10 | 6 | 16 | 8 / 8 |
| `learn` (A) | 16 | 4 | 25 | 16 / 16 |

**Supports the workflows:** yes. Conversations → messages → citations, grounding reviews and traces
cover the answer trail; `kb` covers documents, chunks, catalogue (programmes, fees, dates), curated
answers with history and versions; `learn` covers events, proposals, eval runs and the self-learning
lessons; `app` covers people, consents, leads, handoffs, notifications, webhooks and audit.

**Isolation:** every tenant table has RLS, and the tenant is set per transaction
(`set_config('app.tenant_id', …, true)`), so a pooled connection never carries another college's
id. No module reads or writes another module's schema (checked: no cross-schema SQL in any module).
The tables without RLS hold no tenant data (`schema_migrations`, `kb.sandboxes`); `app.rate_hits`
has RLS on but not forced and stores only hashed keys.

**Relationships to tighten (P2):**
- `chat.voice_audio` and `chat.wa_messages` carry `conversation_id` without a foreign key; add
  `references chat.conversations on delete cascade` so erasure cannot leave orphans.
- `learn.proposals` has no foreign keys to what it changes; acceptable, since targets live in `kb`
  and versions are recorded, but add a check that a deployed proposal names its knowledge version.

**Indexes (P2, matters only at scale):** 32 tenant tables have no index starting with `tenant_id`.
Most are reached by another key (`trace_id`, `conversation_id`), so this is fine now. When a table
is listed per tenant (notifications, consents, outbox), add `(tenant_id, created_at desc)`.

## 5. Query efficiency (measured on RUAS: 102 documents, 1,445 chunks, 214 curated answers)

| Endpoint | First call | Then | Size |
|---|---|---|---|
| C `/analytics/overview?days=30` | 37 ms | 21 ms | 2 KB |
| C `/analytics/topics` | 34 ms | 27 ms | 2 KB |
| C `/approvals` | 61 ms | 60 ms | 33 KB |
| C `/curated-qa` | 91 ms | 83 ms | **136 KB** |
| C `/kb/documents` | 19 ms | 19 ms | 56 KB |
| C `/today` | 60 ms | 61 ms | — |
| A `/kb/search` (hybrid + rerank) | 1,824 ms | 47 ms (memo) | 10 KB |

All well inside the PRD's dashboard target (< 3 s). Two things grow with content:

- The search cache checks a freshness stamp with five `count`/`max` sub-queries before every search
  (`search.py:41`). Cheap at 1,445 chunks; at 50,000 it becomes a cost on every question. **Fix:**
  bump a per-tenant `kb.versions` counter on write and compare one row.
- Without pgvector on this machine, vectors are searched in memory per process (Phase 2, F4.7). On
  the server, install pgvector so the HNSW path in `search.py` is used.

## 6. Transactions and data consistency

- `tx()` in each module wraps a request in one transaction, sets the tenant, commits or rolls back;
  pooled connections are reset after use. ✅
- C → A and C → webhooks use an **outbox** (`app.outbox_events`, `app.message_outbox`,
  `webhook_deliveries`) with `for update skip locked` and retry counts. ✅ The right pattern.
- A's self-learning worker claims events with `skip locked` and retries three times. ✅
- **B → C has no outbox (B2).** Lead, consent, handoff and feedback calls are synchronous; on failure
  the visitor hears "could not save" (`voice_turns.py:200`) or the WhatsApp opt-in is recorded as
  `false` and the conversation continues (`whatsapp.py:824`). **Fix:** a small `chat.outbox`
  table with the same claim-and-retry loop C already uses; tell the visitor "saved" once it is
  queued.
- Erasure crosses all modules through events and is tested in the walking-skeleton check. ✅

## 7. Scalability

Today each service runs as one process. That is fine for the pilot's 20–40 chats and 5 voice
sessions, but these will break as soon as a second worker or a rolling restart is used (B1):

| State | Where | Effect with 2+ workers or a restart |
|---|---|---|
| Retell call registry | `assistant/retell.py:39` | a call routed to the other worker is refused; a restart drops live calls |
| Vapi call registry | `assistant/vapi.py:57` | same for phone calls |
| Visitor rate limits | `assistant/guard.py:105` (in memory) | each worker allows the full limit (C already stores its limits in the database) |
| Live-turn counter | `assistant/engine.py:190` | load reporting is per worker |
| Programme and fallback caches | `experience/routes/programmes.py`, `fallback.py` | harmless (time-limited copies) |
| Search index cache | `knowledge/search.py:38` | safe: checked against the database stamp on every search |

**Fix:** keep call registries and B's rate limits in the database (`chat.voice_calls`, reuse C's
`rate_hits` pattern). No Redis is needed at this size.

## 8. Logging and error handling

- Uvicorn access logs plus module loggers; traces of every turn in `chat.traces` (the strongest
  observability in the system).
- No structured (JSON) logs and no log level configuration in A and C; `print` is used in 27 places,
  mostly in scripts (`seed.py`, `retrieval_eval.py`) and a few service paths (`messaging.py`,
  `privacy.py`, `webhooks.py`). **Fix:** one `logging.basicConfig` with a JSON formatter per
  service, including `trace_id` and `tenant_id`; replace `print` in service code.
- **Swallowed errors (B4):** Module A's start-up recovery and background sweeps catch `Exception`
  and `pass` (`knowledge/main.py:40-52`), as does sandbox clean-up (`gate.py:1633`). A failure there
  leaves no trace. **Fix:** `log.exception(...)` in each, keeping the "never block start-up"
  behaviour.
- Failed staff sign-ins are rate-limited but not logged or audited (console review R5).

## 9. Configuration management

- Each service has a `Settings` dataclass reading the environment (about 136 variables in total),
  with `.env.example` files and `local.env.sh` / `local.secrets.sh` for local runs. Secrets are not
  in the code. ✅
- `APP_ENV` defaults to `production`, and production refuses development settings. ✅ A safe default.
- Some behaviour-changing values live only in `local.env.sh` (for example `GEMINI_FIRST_TOKEN_S=15`,
  which overrides the code default of 6 s and causes the 15-second waits seen in Phase 3). **Fix:**
  keep tuning defaults in code; let `local.env.sh` set only machine-specific values.

## 10. Duplicated logic, coupling and dependencies

| Item | Where | Recommendation |
|---|---|---|
| PII masking (B3) | A `pii.py`, B `guard.py` — different e-mail patterns; both miss OTPs and names | One implementation and one shared test file of examples; vendor it into each module if modules must stay independent, but keep the test cases identical |
| `problems.py` | byte-identical in A, B, C | Acceptable for independent modules; note it in the README so a fix is applied to all three |
| `db.py` (`tx`, pool) | near-identical in A and B, larger in C | Same as above |
| A ↔ B calls (B6) | A calls B to run evaluation questions; B calls A to search | Keep, but make both tolerate the other being down at start-up (they mostly do) and document the start order |
| Module B's local speech stack | `faster-whisper`, `piper-tts`, `livekit` always installed | Move to an optional `requirements-voice-local.txt` so a hosted-voice deployment is smaller |
| Next.js 16.4.3 | known vulnerability; Dependabot's 16.4.5 is waiting on `Hpai-lab1/admitai` | Upgrade on a branch and run the web tests |
| Size of single units | `engine._turn` ~650 lines, `gate.py` 1,738, `whatsapp.py` 1,280 | Split `_turn` into steps (language, routing, catalogue, retrieval, answer, follow-ups) without changing behaviour; covered by the existing DB tests |

Dependencies are otherwise lean and pinned exactly (no ORM, no task queue, ONNX instead of PyTorch
for the reranker).
