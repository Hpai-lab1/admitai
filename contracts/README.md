# Contracts (owner: guide)

The API contracts between modules. **Draft in week 1, frozen at the end of week 2**; after that every change is a pull request approved by the guide, with a version bump.

| File | Provided by | Consumed by |
|---|---|---|
| `knowledge.openapi.yaml` | Module A | B (search, catalogue facts), C (admin, curated Q&A, improvements, evals) |
| `assistant.openapi.yaml` | Module B | C (chat stream, voice token), A (headless chat for tests) |
| `experience.openapi.yaml` | Module C | B (handoffs, consents, leads) |
| `events.schema.json` (to add) | C → A | `feedback.created`, `correction.submitted` |

The three OpenAPI files here are **stubs**: they list the agreed endpoints so mocks can start. Request and response schemas are filled in during week 1.

Rules:
- Every service is checked against its contract in CI on each pull request.
- Every message carries a `trace_id`, and answers carry the agent, prompt and model versions used (PRD 11.2), so feedback can be attributed.
- Mocks: Module A publishes a mock `/kb/search` and Module B a mock chat stream by the end of week 2.
