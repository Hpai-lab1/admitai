# Module B contract proposal v0.3 (for the guide)

`contracts/assistant.openapi.yaml` is still the week-1 stub: three paths, no schemas. Module B
implements 28 operations that Modules A and C already call. The repository rule is that the
contract wins, so the agreed contract should describe what is built. This proposal is generated
from the code (`assistant.openapi.json` here, OpenAPI 3.1 with request and response schemas) and
only describes the API; adopting it changes no behaviour.

**Asked of the guide:** replace the stub with `assistant.openapi.json` (version 0.3.0), or say which
operations should change first. Changes since the last proposal (v0.2 in
`docs/proposed-contracts/`): one new operation, `POST /messages/lookup`.

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
- `done`: last event: `conversation_id`, `message_id`, `trace_id`, `outcome` (answered / partial / no_answer / handoff / blocked / refused / error), `confidence`, `offer_handoff`, `language`, `route`, `sources[]`, `versions` (agent, prompt, model).

`lead_prompt` carries `score` (0-100), `band` (hot/warm/cold) and `parts`. The weights and bands are
per college (`lead_scoring` in pack.yaml, F5.4); the PRD fixes the scale and the bands, not the values.

## Needed from Module A's contract

Module B also calls `GET /catalogue/programs` (optionally `?facts=true`) to recognise programme
names in a question. It is implemented in Module A but is not in `contracts/knowledge.openapi.yaml`;
it should be added there at the same time.
