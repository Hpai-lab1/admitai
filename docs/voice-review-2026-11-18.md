# Voice assistant review (18 Nov 2026)

Scope: every way a visitor can speak to the assistant — the website (hands-free call and
push-to-talk), the telephone, and WhatsApp voice notes — in English, Hindi and Kannada.

**Rule used in this review:** a channel or language is called *working* only if it was seen
working end to end. Each item is marked with one of these, plus the evidence:

| Mark | Meaning |
|---|---|
| ✅ Tested, working | seen working end to end, with the evidence named |
| 🟡 Works partly | seen working, but with failures or only some cases |
| 🧪 Built, not tested | the code exists and its unit tests pass, but it was never seen working on a real channel |
| 📝 Planned | in the PRD or README, not built |
| ⛔ Not adopted | considered and deliberately not built |

Evidence collected on 18 Nov:
- **Retell's own call log** (`POST /v3/list-calls`): 74 web calls, 13–18 Nov, with Retell's
  measured end-to-end latency per call.
- **Vapi's call log** (`GET /call`): every phone call ever placed to the number.
- **Database:** voice turns by channel and language (`chat.messages`).
- **Push-to-talk round trip:** six questions (two per language) spoken by the system's own voice
  (`/voice/speak`) and sent back through `/voice/turn`. Synthesised speech is cleaner than a real
  caller, so this proves the pipeline, not real-world accuracy.
- **One live Retell call** from earlier today, read back from Retell (47 s, English).

## 1. Architecture (as built)

```
Website, hands-free call ─▶ Retell (Soniox hears, ElevenLabs "Monika" speaks)
                               │  custom-LLM WebSocket through a cloudflared tunnel
                               ▼
Website, push-to-talk ─────▶ Module B /voice/turn ─▶ Sarvam Saaras (hears) ─▶ engine ─▶ Sarvam Bulbul / Piper (speaks)
                                                      (local Whisper if Sarvam fails)
Telephone ─────────────────▶ Vapi ─▶ /voice/vapi/webhook + /voice/vapi/chat/completions ─▶ engine
WhatsApp voice note ───────▶ Meta webhook ─▶ Sarvam or Whisper ─▶ engine ─▶ voice note back
LiveKit (WebRTC) ──────────▶ voice_worker.py (only if LIVEKIT_URL is set)
```

All channels share one engine, the same knowledge, the same sentence checks and the same
conversation record, so a call and a chat in one widget session are one conversation.

## 2. Status by channel

| Channel | Status | Evidence |
|---|---|---|
| **Website hands-free call (Retell)** | 🟡 Works partly | 74 calls: **37 ended normally** (visitor hung up); **16 `error_llm_websocket_runtime`**, **12 `error_llm_websocket_open`** (Retell could not reach this service — the quick tunnel had changed or was down), **9 `error_user_not_joined`**. A 47-second English call today answered "What is the process for admissions?" correctly. |
| **Website push-to-talk** (`/voice/turn`) | ✅ Tested, working | 6 of 6 synthetic round trips heard correctly, answered and spoken back (§4); 71 real `web_voice` turns in the database (55 English, 7 Hindi, 9 Kannada) |
| **Telephone (Vapi)** | ⚠️ **Not working** | Vapi has logged **one call ever** (17 Nov), ended with `assistant-request-returned-error` — this service's answer to Vapi's set-up request failed. No call has completed. The inbound number is configured; outbound needs an Indian number and a card on Vapi. |
| "phone" turns in the database | not real calls | 36 turns on the `phone` channel (13–17 Nov) are the same scripted question in three languages ("What is the fee for B.Tech in Computer Science?" …) — test runs, not callers |
| **WhatsApp voice notes** | 🧪 Built, not tested | code complete (`whatsapp.py`: Sarvam or Whisper, "You said: …", a voice note back); WhatsApp itself is not connected (no token) |
| **LiveKit (WebRTC) voice** | 🧪 Built, not tested | `voice_worker.py`, tokens from `/voice/token`; `LIVEKIT_URL` not set, never run |
| Telephony via SIP / Indian provider, DTMF menu, warm transfer (PRD F2.7) | 📝 Planned (Level 2) | — |
| **Bolna.ai** | ⛔ Not adopted | `assistant/README.md:804`: "a call platform, the role Retell already has". A `BOLNA_KEYS` value in `local.secrets.sh` is unused and can be removed. |

## 3. Status by language

| Language | Push-to-talk | Retell call | Telephone | Notes |
|---|---|---|---|---|
| English | ✅ 2/2 round trips; 55 real turns | ✅ calls answered (today's call) | ⚠️ never completed | |
| Hindi | ✅ 2/2 heard and answered; 7 real turns | 🧪 agent configured for `hi-IN`; no Hindi call found in this review | ⚠️ | answers depend on the Hindi path (5/10 in the chat test set) |
| Kannada | 🟡 2/2 heard correctly; 1 of 2 answered ("installments" got "no reliable information"); 9 real turns | 🧪 agent configured for `kn-IN` with Soniox; no Kannada call found in this review | ⚠️ | Kannada is Level 2 in the PRD; chat test set 9/16 |
| Code-mixed (Kanglish, Hinglish) | ✅ transcripts come back mixed ("Robotics Engineering B.Tech ಶುಲ್ಕ ಎಷ್ಟು?") and are answered | 🧪 Soniox supports it; not tested here | — | |

**Language detection:** Sarvam Saaras detects the spoken language (`saaras:v4`, code-mix mode);
the widget's EN / हिं / ಕ choice is passed as a hint. In Retell calls, Soniox recognises the three
configured locales and the engine decides the reply language each turn (`language.decide`). The
typed-language rule decided on 17 Nov applies to speech too: English speech is answered in English
unless the visitor set a reply language.

## 4. Speech-to-text and text-to-speech

**Push-to-talk round trip (synthetic speech, 18 Nov):**

| Said | Heard | Reply | Time |
|---|---|---|---|
| en: What is the fee for B.Tech in Robotics Engineering? | identical | ₹4,00,000 per year [1] (catalogue) | 0.9 s |
| en: Is there negative marking in RUASAT? | identical | "There is no negative marking…" | 11.1 s |
| hi: रोबोटिक्स इंजीनियरिंग बी.टेक की फीस कितनी है? | "Robotics, engineering, B.Tech की fees कितनी है?" | Hindi, ₹4,00,000 [1] | 4.8 s |
| hi: क्या फीस किस्तों में भर सकते हैं? | "क्या fees किस्तों में भर सकते हैं?" | Hindi, correct ("not allowed in installments") | **43.2 s** |
| kn: ರೋಬೋಟಿಕ್ಸ್ ಎಂಜಿನಿಯರಿಂಗ್ ಬಿ.ಟೆಕ್ ಶುಲ್ಕ ಎಷ್ಟು? | "Robotics Engineering B.Tech ಶುಲ್ಕ ಎಷ್ಟು?" | Kannada, ₹4,00,000 [1] | 3.5 s |
| kn: ಫೀಸ್ ಅನ್ನು ಕಂತುಗಳಲ್ಲಿ ಕಟ್ಟಬಹುದಾ? | "fee-ಅನ್ನ ಕಂತುಗಳಲ್ಲಿ ಕಟ್ಟಬಹುದಾ?" | "no reliable information" | 13.8 s |

Every transcript kept the meaning; every reply came with audio in its own language.

| Engine | Used for | Status |
|---|---|---|
| Sarvam Saaras v4 (STT) | push-to-talk, WhatsApp voice notes | ✅ (round trip above) |
| Sarvam Bulbul v4-flash (TTS) | Hindi and Kannada replies | ✅ (round trip above) |
| Piper (local TTS) | English replies | ✅ (round trip above) |
| faster-whisper `base` (local STT) | fallback when Sarvam fails | 🧪 weak on Kannada by the team's own benchmark (`assistant/benchmarks/stt-2026-11-13.json`) |
| Soniox (via Retell) | Retell calls | ✅ English; 🧪 Hindi/Kannada |
| ElevenLabs `eleven_v3` "Monika" (via Retell) | Retell calls, since 18 Nov (one voice for the whole call) | 🧪 changed today; not yet heard by this review |

**Not measured:** real-speaker accuracy (word error rate) with RUAS staff or students, background
noise, and Indian-English accents. The synthetic test cannot show these.

## 5. Latency

PRD L1 target: end of speech to first audio ≤ 3.5 s p95.

| Path | Measured | Against target |
|---|---|---|
| Retell calls (Retell's own end-to-end figure, last 20 calls) | per-call median **2.8–5.0 s**, most 3.6–4.9 s | ⚠️ mostly above 3.5 s |
| Push-to-talk, catalogue answers | 0.9–4.8 s (full turn incl. speech) | ✅ within or close |
| Push-to-talk, model answers | 11–43 s | ❌ far above |

The model path is slow for the reasons in the AI review: Gemini's response time on the free key,
and three sequential model calls for Hindi and Kannada. The Retell path hides part of it with a
spoken "One moment, let me check that."

## 6. Session management and conversation continuity

- One conversation id for chat and voice in the same widget session ✅ (by design; follow-ups such
  as "what about its fees?" resolve across turns — tested in chat).
- Voice tokens are short-lived and signed (`VOICE_TOKEN_SECRET`); a Retell call is accepted only
  for a call id this service created in the last hour.
- **Retell and Vapi calls are remembered only in Module B's memory** (`retell.py:39`, `vapi.py:57`):
  a restart of Module B ends every live call, and a second worker cannot serve them.
- The quick tunnel's address changes on every restart; 12 of 74 Retell calls failed because Retell
  could not reach the service.

## 7. Error recovery

| Situation | What happens | Status |
|---|---|---|
| Gemini slow or failing during a call | backup request after 1.5 s, one retry on 503, then the local model; "One moment" said meanwhile | ✅ by code; 🧪 not timed on calls |
| Sarvam down or audio over 30 s | Whisper hears it; English falls back to Piper; Hindi/Kannada replies shown as text, never read by the English voice | 🧪 by code and unit tests |
| Speech not understood | "Sorry, I didn't catch that" in the visitor's language; wrong-script transcripts treated as mishearing | ✅ by code and tests |
| Retell cannot reach the service | the call fails (`error_llm_websocket_open`); the widget falls back to push-to-talk only if the call could not start | ⚠️ 12 failed calls |
| WebSocket breaks mid-call | call ends (`error_llm_websocket_runtime`, 16 calls) | ⚠️ no reconnect |
| All voice slots busy (5) | "busy" message in the visitor's language (PRD 10.2) | ✅ by code |

## 8. Human handoff from voice

- **Spoken lead capture (F2.9):** phone numbers read back digit by digit in English, Hindi and
  Kannada before saving; abandoned after ten minutes ✅ (unit-tested; `spoken_lead.py`).
- **"Talk to a person"** works by voice the same way as in chat (callback offered, office hours
  said) ✅ by code; ⚠️ if Module C is down, the spoken number is lost (no retry — backend review B2).
- **Warm transfer to a staff phone** (PRD F2.7, L2): 📝 not built.
- **Spoken "was this helpful?" at the end (F8.2):** ✅ for push-to-talk; 🧪 not wired into Retell
  calls.

## 9. Gaps against the PRD's voice design

| PRD (L1) | Built instead | Note |
|---|---|---|
| Browser voice over LiveKit (F2.1) | Retell hands-free calls + HTTP push-to-talk | LiveKit worker built but never run |
| faster-whisper on GPU (F2.2) | Sarvam (hosted) + Soniox (via Retell); Whisper as fallback | hosted, outside India — privacy finding in the PRD review |
| Piper TTS, English; Hindi best-effort (F2.4) | Piper English; Sarvam Hindi/Kannada; ElevenLabs in calls | more languages than L1 asks; hosted |
| Barge-in ≤ 300 ms (F2.4) | Retell interruption sensitivity 0.8 | not measured |
| ≤ 3.5 s p95 (F2.5) | 2.8–5.0 s per Retell call | not met yet |
| Audio stored only with consent, retention (F2.6) | consented storage for push-to-talk (migrations 009, 010) | ⚠️ Retell keeps its own recordings outside this rule; `assistant/README.md` still says "audio is never stored", which is no longer true for consented push-to-talk |

## 10. Recommendations (in order)

1. **Fix the telephone path before claiming phone support.** Reproduce the
   `assistant-request-returned-error` with one call, log the webhook's response, and add a test
   with Vapi's recorded request body. Until then, present telephony as "in integration".
2. **A stable public address for Module B** (a named Cloudflare tunnel or the lab server) so Retell
   and Vapi do not lose the service on each restart; target: 0 `error_llm_websocket_open` per week.
3. **Store call registries in the database** so a restart does not end live calls.
4. **Measure real speech:** 20 recorded questions per language from RUAS staff or students (with
   consent), reporting word error rate and answer correctness; repeat after each provider change.
5. **Voice latency:** curated answers pre-translated (AI review §6), `GEMINI_FIRST_TOKEN_S=6`, and
   Retell's latency figure shown on the Analytics page; target: Retell e2e p50 ≤ 3.5 s.
6. **Test Hindi and Kannada Retell calls** once each and record them in this table before saying
   the call channel supports them.
7. **Privacy:** per-college opt-in for Retell / Sarvam / Vapi / ElevenLabs, recording storage off in
   the Retell agent, and the README corrected about stored audio.
8. Remove the unused `BOLNA_KEYS` value.

## 11. What can be claimed today

- **Can be claimed:** website voice in English, Hindi and Kannada through push-to-talk (heard,
  answered with sources, and spoken back); hands-free website calls in English through Retell, with
  about half of past calls failing on connection problems.
- **Cannot be claimed yet:** telephone calls (none has completed), WhatsApp voice notes (WhatsApp
  not connected), Hindi or Kannada hands-free calls (not tested), real-speaker accuracy, and the
  PRD's 3.5-second voice latency.
