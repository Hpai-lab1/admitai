# Module B contract proposal v0.4 (for the guide)

`contracts/assistant.openapi.yaml` is still the week-1 stub: three paths, no schemas. Module B
implements 28 operations that Modules A and C already call. The repository rule is that the
contract wins, so the agreed contract should describe what is built. This proposal is generated
from the code (`assistant.openapi.json` here, OpenAPI 3.1 with request and response schemas) and
only describes the API; adopting it changes no behaviour.

**Asked of the guide:** replace the stub with `assistant.openapi.json` (version 0.6.0), or say which
operations should change first.

Changes in v0.6 (17 Nov 2026, phone calls through Vapi; all additive):
- `POST /voice/vapi/calls` (service keys; Module C's key is allowed): `{"to": "+91..."}` places an
  outbound call through Vapi; reply `201 {"call_id", "status", "conversation_id"}`; 503 when Vapi
  is not configured, 502 when Vapi refuses. Module C calls it only for a person who agreed to be
  contacted; Module B does not store the number.
- `POST /voice/vapi/webhook` and `POST /voice/vapi/chat/completions`: called by Vapi only
  (shared secret or a call this service created); not for Modules A or C.

Changes in v0.5 (16 Nov 2026, English / Hindi / Kannada; all additive: an old caller keeps working
unchanged):
- `POST /chat/{conversation_id}/messages`: `language` keeps its meaning for English questions (the
  reply language the visitor picked, EN / हिं / ಕ); a Hindi or Kannada question is now answered in
  that language whatever was picked, and "reply in Kannada" / "हिंदी में बताइए" in the message
  sets the reply language for the rest of the conversation (`language.py`). The `done` event adds
  `input_language`, `language_reason` (`requested` / `same_as_input` / `continuing` /
  `preference` / `default`), `code_mixed` and `output_mode` (`text` / `voice` / `both`, or null).
- `POST /voice/turn` takes two optional form fields: `response_language` (`en`/`hi`/`kn`, the reply
  language for English speech; default `language`) and `output` (`text` = no audio; `voice` /
  `both` = audio in the reply's language when a voice exists for it). The reply adds
  `input_language`, `output_mode` and `providers` (`{"stt", "tts"}`: `local`, `sarvam` or null);
  `timings` is unchanged.
- `POST /voice/speak` takes an optional `language` (`en` default, `hi`, `kn`): Hindi and Kannada
  are spoken by Sarvam when it is configured; without a voice for the language: 422.
- WhatsApp webhook: `audio` messages (voice notes) are accepted, heard and answered; a voice note
  is answered with text and a voice note (MP3 or OGG/Opus) by default.

Changes since v0.3 (all additive: an old caller keeps working unchanged):
- `POST /voice/token` takes two optional fields: `language` (`en`/`hi`/`kn`, the visitor's choice,
  so the LiveKit worker listens for it from the first word) and `audio_consent` (PRD F2.6). The
  reply adds `audio_recording` (`off` / `offered` / `on`) and `audio_days`.
- `POST /privacy/erase` takes an optional `scope`: `all` (default, as before) or `audio` (the visitor
  withdrew `audio_recording` consent: only their recordings are deleted). The reply adds
  `recordings`.

Changes in v0.3 (from v0.2 in `docs/proposed-contracts/`): one new operation, `POST /messages/lookup`.

## Operations by caller

| Caller | Operations |
|---|---|
| Visitor (widget, token from Module C) | `POST /chat/{conversation_id}/messages` (SSE), `GET /chat/{conversation_id}/last`, `POST /voice/token`, `POST /voice/turn`, `POST /voice/speak` |
| Module C (service key) | `GET /chat/{conversation_id}`, `GET /messages/{message_id}`, `POST /messages/lookup`, `GET /analytics/turns`, `GET /analytics/topics`, `POST /privacy/erase`, `POST /channels/whatsapp/guardian-code`, `POST /channels/whatsapp/notify` |
| Module A (service key) | `GET /signals`, `POST /reviews/messages`, `GET /reviews/unsupported`, `GET /load`, `POST /voice/eval-result`, `PUT/GET /tenant-settings`, `DELETE /tenant-data`, `POST /tenant-pack`, `POST /tenant-pack/validate` |
| Operators (ops key) | `POST /reviews/run`, `GET /traces/{trace_id}` |
| Meta (signed) | `GET/POST /channels/whatsapp/webhook` |
| Anyone | `GET /health` |

## The chat stream (`POST /chat/{conversation_id}/messages`)

Server-sent events, in this order:

- `token`: one sentence that passed the deterministic checks (`{"text": ...}`); never a partial sentence.
- `handoff_requested`, `consent_required`, `lead_prompt`: when the turn needs them.
- `done`: last event: `conversation_id`, `message_id`, `trace_id`, `outcome` (answered / partial / no_answer / handoff / blocked / refused / error / clarify), `confidence`, `offer_handoff`, `language` (the reply's), `input_language`, `language_reason`, `code_mixed`, `output_mode`, `route`, `sources[]`, `versions` (agent, prompt, model).

`lead_prompt` carries `score` (0-100), `band` (hot/warm/cold) and `parts`. The weights and bands are
per college (`lead_scoring` in pack.yaml, F5.4); the PRD fixes the scale and the bands, not the values.

## Needed from Module A's contract

Module B also calls `GET /catalogue/programs` (optionally `?facts=true`) to recognise programme
names in a question. It is implemented in Module A but is not in `contracts/knowledge.openapi.yaml`;
it should be added there at the same time.

## LiveKit voice (PRD F2.1, F2.2, F2.4, 9.3): messages inside the room

`POST /voice/token` returns `livekit: {url, room, token}` when LiveKit is set up. The browser joins
that room and publishes its microphone; Module B's voice worker (`python -m
assistant_api.voice_worker`) joins as `assistant` and speaks on its own audio track. Control and
captions travel as LiveKit data messages on topic `admitai` (JSON), not over HTTP, so they are not
OpenAPI operations; they are listed here so Module C can rely on them.

Browser -> worker: `{"type": "language", "lang": "en"|"hi"|"kn"}`, `{"type": "mode", "mode":
"hands_free"|"ptt"}`, `{"type": "ptt", "down": true|false}`.

Worker -> browser: `state` (`idle` / `listening` / `thinking` / `speaking`, for the voice orb, PRD
13.4), `caption` (`text`, `final`: live captions while the visitor speaks, then the transcript),
`answer` (one checked sentence, as it is spoken), `barge_in` (`stop_ms`), `done` (`outcome`,
`sources`, `message_id`, `offer_handoff`, `timings`), `error` (`stt` / `turn` / `forbidden` / `busy`
/ `no_visitor`).

## Needed from Module C (widget and consent)

1. **Widget LiveKit client.** When `/voice/token` returns `livekit`, join the room and use the
   messages above; otherwise keep push-to-talk. A ready client for the widget is in
   `widget-livevoice.ts` here (uses `livekit-client` 2.23.0). Module B did not edit `experience/`.
2. **Audio consent (F2.6).** When the token reply says `audio_recording: "offered"`, show an
   unticked box ("Keep recordings of my spoken questions for {audio_days} days") and send
   `audio_consent: true` on the next token request when ticked. Module B records the consent
   (purpose `audio_recording`) in Module C before anything is stored.
3. **Withdrawal.** When a visitor withdraws `audio_recording` in `POST /consents/withdraw`, call
   Module B's `POST /privacy/erase` with `{"session_id": ..., "scope": "audio"}`.
4. **Languages and output (v0.5).** The widget's language buttons stay as they are and now mean
   "reply in" for English questions. Three small additions, described with code in
   `widget-multilingual.md` here (Module B did not edit `experience/`):
   - An output choice next to the language buttons: *Text*, *Voice*, *Both* (default *Text* in chat,
     *Both* for the microphone). Send it as `output` on `/voice/turn`.
   - A play button on each assistant message, and auto-play when the output is *Voice* or *Both*:
     `POST /voice/speak {"text", "language": done.language}`; hide the button on a 422 (no voice
     for that language here).
   - Show `done.language` on the message (e.g. a small "ಕನ್ನಡ" tag) so a switch is visible, and
     keep the picked language button in sync when `done.language_reason == "requested"`.
