# Security and privacy audit (18 Nov 2026)

Scope: authentication, authorization and RBAC, database permissions and row-level security, API
and webhook security, secrets, input validation, prompt injection, personal data, session
isolation, logging and exposure of sensitive information — across Modules A, B and C, the web
app, the local database and the public voice tunnel.

This report contains **no keys, passwords, tokens, phone numbers, e-mail addresses or other
personal data**. Where a finding involves a secret or a personal detail, only its kind is named.

## How it was tested

| Area | Method |
|---|---|
| Database roles | role attributes and table owners read from `pg_roles` / `pg_tables`; which role each service connects as (user names only) |
| SQL injection | every SQL statement built with an f-string or `.format()` traced to its inputs |
| Session isolation | live: two visitor sessions; the second tried to read and write the first's conversation; tampered and missing tokens |
| RBAC | live: 8 staff roles × 20 endpoints (console review), writes only to non-existent ids |
| Secrets | `.gitignore` checks; git history searched for key patterns; each value in the local secrets file compared (in memory, never printed) against all git history and all service logs |
| Logs | service logs scanned for phone-number and e-mail patterns (counts only) |
| CORS and headers | preflight from a foreign origin; response headers of the site, widget and console |
| Public exposure | unauthenticated requests through the live cloudflared tunnel to Module B |
| Prompt injection | 5 live probes (AI review) |
| Code | upload, zip, crawler, webhook and token handling |

## Summary

The foundations are strong. Tenant isolation is enforced by the database (RLS on every tenant
table, non-superuser roles for request handling, tenant set per transaction); there is no SQL
injection; sessions are isolated; webhooks are signature-checked in constant time; secrets have
never been committed; logs hold no keys or personal contact details; and every service refuses to
start in production with development settings.

The serious problems are **privacy**, not classic vulnerabilities: personal data reaching outside
services without the college's opt-in, masking that misses one-time codes and names, retention
rules that never run, and a super-admin who can read visitor comments without break-glass.

| Severity | Count |
|---|---|
| Critical | 4 |
| High | 5 |
| Medium | 9 |
| Low | 5 |

## Critical — fix before any real applicant uses the system

| # | Finding | Impact | Fix |
|---|---|---|---|
| C1 | **Voice providers receive raw personal data without the college's opt-in.** Retell (with Soniox and ElevenLabs), Sarvam and Vapi are switched on for every college as soon as the platform has a key (`routes/voice.py:98`, `sarvam.py:144`, `vapi.py:106`). They receive the visitor's voice and full transcripts — including spoken phone numbers and names — unmasked, processed outside India. Retell also keeps call recordings under its own retention. | Breaks the PRD's privacy rules (L1: no external model calls, data in India; L2: per-college opt-in, PII redaction, data-processing agreement) and DPDP purpose limitation; students under 18 are a core audience. | Gate every external voice service per college, like Gemini (`model_policy.external`), default off; switch off call recording / storage in the Retell agent; a data-processing agreement per provider; a short spoken and on-screen notice before a call starts. |
| C2 | **Masking misses one-time codes and names.** `mask_pii` (B `guard.py`) and A `pii.py` mask phone numbers, e-mails and ID numbers, but not a six-digit OTP or a person's name (verified with test strings). | A visitor's OTP and another student's name were shown on the Analytics page under "personal details are masked" and in the approval inbox; the same unmasked text is sent to Gemini (masking runs before Gemini, so its gaps reach Google). | One shared masking function with one shared set of test cases; mask 4–8-digit codes near OTP / PIN / code / password and names after "my name is / of / for"; never list questions flagged as account-access or personal-record requests. |
| C3 | **Super-admin reads a college's raw feedback** — conversation ids and visitors' free-text comments — without break-glass (`GET /feedback` returned 200 with full rows). | PRD 5.3: super-admin gets aggregates only and "no access to tenant PII unless break-glass is used". A platform operator can read what applicants wrote. | Return aggregates to `super_admin` unless an active break-glass session exists; audit each read during break-glass. |
| C4 | **Two provider API keys were pasted into a chat session** during set-up (a speech provider and a telephony provider) and have not been rotated. | Anyone with access to that chat history can use the accounts (cost, data access on the provider side). | Rotate both keys in the providers' dashboards and update the local secrets file. Treat any key ever pasted into chat, tickets or documents as exposed. |

## High

| # | Finding | Impact | Fix |
|---|---|---|---|
| H1 | **Running services hold a superuser database connection.** Requests use non-superuser roles, but Module A (evaluation gate, learning) and Module C (outbox, webhooks, web push) open connections as the `postgres` superuser at run time (`gate.py:576–1717`, `learning.py:745`, `webhooks.py:203`, `webpush.py:117`). | Any code-execution or injection bug in those processes gives full control of every college's data and, through superuser features, of the database server. | A dedicated `admitai_worker` role with `BYPASSRLS` and grants only on the tables those jobs touch; keep the superuser for migrations only. |
| H2 | **No password reset, and temporary passwords never have to be changed.** New staff get a temporary password shown once to the admin, who passes it on; nothing forces a change at first sign-in; there is no "forgot password" and the admin can reset only two-factor. | Temporary passwords travel over chat or e-mail and stay valid indefinitely; locked-out staff need a developer. | Force a new password at first sign-in; admin "reset password" issuing a new one-time password; later an e-mailed reset link with a short expiry. |
| H3 | **Retention rules never run.** `retention_sweep` (B and C) and `purge_old_audit` exist only as command-line entry points; nothing schedules them (no cron, launch agent or deploy job). | Conversations, consents, audio and audit rows are kept beyond the configured retention (PRD 11.4; DPDP storage limitation). | Run the sweeps daily from the services' existing background loops (as the outbox already does), and show "last retention run" on the Admin page. |
| H4 | **Crawler fetches any address.** A source's `start_url` is not validated and redirects are followed to any host (`crawl.py:35`, `site_kb.py:211`); only links are kept to the start host. | Today only developers add sources. Once source management reaches the console (recommended in the console review), a college admin — or a college page that redirects — can make the server fetch internal addresses (other modules, cloud metadata), and the result becomes answerable in chat. | Allow only `https` on public addresses; resolve the host and refuse private, loopback and link-local ranges, re-checking after every redirect; limit redirects to the same registrable domain. |
| H5 | **A college's widget can be used from any website.** The public key is in the embed script; widget sessions are not tied to the college's domains; the widget is framable anywhere (`frame-ancestors *`). | Another site can embed a college's assistant (impersonation), and scripted sessions can spend the college's paid Gemini, Retell and Sarvam quota; only per-IP and per-tenant rate limits stand in the way. | Per-college allowed origins in the pack; check `Origin` / referrer on `POST /widget/session`; set `frame-ancestors` to those origins. |

## Medium

| # | Finding | Impact | Fix |
|---|---|---|---|
| M1 | **Public tunnel publishes Module B's API description.** Through the cloudflared address, `/docs` and `/openapi.json` answer without a key (every other endpoint tested returned 401). The quick tunnel runs with `APP_ENV=development`. | A complete map of Module B's endpoints for anyone who finds the address. | Disable docs when not on localhost, or expose only the voice paths through the tunnel (Cloudflare ingress rules). |
| M2 | **No "sources are data, not instructions" rule in the answer prompt** (`prompts.SAFETY_RULES`). | A crawled page containing instructions could steer wording; numbers and promises are still caught by the sentence checks. Direct injection was refused in all live probes. | Add the rule; add injection probes to the test set. |
| M3 | **Missing security headers.** The console sends `X-Frame-Options: DENY` and `Referrer-Policy: no-referrer`, but no Content-Security-Policy, `X-Content-Type-Options: nosniff` or `Permissions-Policy` (microphone); the public pages send none. | Less defence against injected scripts and clickjacking on the public pages. | A CSP for the console and widget (scripts from self and the Retell SDK only), `nosniff`, and `Permissions-Policy: microphone=(self)`. |
| M4 | **Failed sign-ins are not audited** (rate-limited only). | Password guessing leaves no trace for the college admin. | Audit `auth.login_failed` with a hashed address. |
| M5 | **Role changes and deactivation apply without confirmation** (`admin/page.tsx:102`). | A slip changes a person's access; it is audited but not prevented. | Save button or confirmation naming the person and both roles. |
| M6 | **Consent record can be lost.** If Module C is down, Module B records a WhatsApp opt-in as not saved and the conversation continues (`whatsapp.py:824`); a spoken phone number is dropped (`voice_turns.py:200`). | Conversations without a stored consent record; leads lost. | An outbox in Module B with retry, like Module C's. |
| M7 | **Module B's visitor rate limits are in memory** (`guard.py:105`). | With more than one worker each allows the full limit. | Store them in the database as Module C does. |
| M8 | **Next.js 16.4.3** has a known vulnerability (the dev server prints the advisory). | Depends on the advisory; the version is outdated. | Upgrade to the patched release (16.4.5 is waiting as a Dependabot branch on the guide's repository) and run the web tests. |
| M9 | **README says audio is never stored**, but consented push-to-talk audio is stored (migrations 009–010). | The privacy notice and the documentation disagree with the system. | Correct the README and the visitor notice. |

## Low

| # | Finding | Fix |
|---|---|---|
| L1 | Service logs contain three provider identifiers (an agent id, a tenant id, a phone-number id) — identifiers, not credentials | Log only the last four characters |
| L2 | An unused provider key (`BOLNA_KEYS`) in the local secrets file | Remove it and revoke it at the provider |
| L3 | `app.rate_hits` has RLS enabled but not forced (holds only hashed keys) | Force it for consistency |
| L4 | Lead search passes `%` and `_` from the search box into `LIKE` unescaped | Escape them |
| L5 | `/docs` is also open on localhost for every module | Acceptable locally; disable in production |

## What is already right (verified)

| Control | Evidence |
|---|---|
| Tenant isolation in the database | RLS on all 60 tenant tables, forced on 59 (all but `app.rate_hits`); tenant set per transaction (`set_config(..., true)`); request handling as `admitai_app`, `admitai_chat`, `admitai_kb` — none superuser, none `BYPASSRLS` |
| No SQL injection | every formatted SQL statement interpolates only fixed table names or numbers; user input always goes in as parameters |
| Session isolation (live) | another visitor reading or writing a conversation: 403 / 404 without content; tampered or missing token: 401 |
| Staff RBAC (live) | server-side checks matched the PRD matrix for 8 roles; department roles see only their department |
| Passwords and sign-in | scrypt, constant-time comparison with a dummy hash for unknown users, rate-limited sign-in and two-factor, TOTP for admins, refresh tokens |
| Visitor tokens | RS256 signed by Module C; Module B holds only the public key |
| Service-to-service | one random key per caller pair, compared in constant time, plus a per-caller allow-list of paths |
| Webhooks | Meta: HMAC-SHA256 of the raw body; Vapi: shared secret in constant time, unknown calls refused; Retell: only call ids this service created in the last hour |
| CORS | allow-list of the web app's origin; a foreign origin gets no CORS headers |
| Uploads | 50 MB limit per document; Tenant Pack zip: size and file-count limits, absolute paths, `..` and links refused |
| Secrets | secrets file, keys and `.env` files git-ignored; no key-like file tracked; no key pattern and none of the 25 local secret values anywhere in git history or in the service logs |
| Logs | no phone numbers or e-mail addresses found in service logs; traces store masked text |
| Production guard | all three services refuse to start outside development with development keys, echoed codes or the two-factor bypass |
| Prompt injection (direct) | instruction-override, prompt-dump, false-authority and "translate a guarantee" probes all refused or blocked |
| Audit | 33 action types including sign-in, export, role change, break-glass, erasure and urgent publishing |

## Order of work

1. C4 rotate the two keys (minutes).
2. C2 masking, C3 super-admin aggregates (one shared change each).
3. C1 per-college opt-in for voice providers; Retell recording off.
4. H3 schedule retention; H2 password reset and forced change.
5. H1 worker role instead of superuser.
6. H5 allowed origins for widgets; H4 crawler address checks (before any console source editing).
7. Medium items with the next release.
