# Module B — Assistant Engine & Channels (Claude Code notes)

Follow the root `CLAUDE.md` first. This module's scope is in `README.md` here.

- Work only inside `assistant/`. Branch prefix: `b/`.
- Database schema you own: `chat`. Never write to `kb`, `learn` or `app`.
- Your API must match `contracts/assistant.openapi.yaml`. If you think the contract needs to change, stop and write down the proposed change for a pull request to the guide.
- Latency budget (PRD 7.5): at most two model calls per turn on the hot path (an optional 8B routing call and one 14B answer call). Any extra step must run asynchronously.
- Sentence checks are deterministic code, not an LLM call: numbers and dates must match Module A's catalogue or a cited chunk; factual sentences need a citation; promise phrases are blocked.
- Level 1 makes **no external LLM calls**. All models go through LiteLLM to the shared Ollama service.
- WhatsApp and voice testing: test numbers and staff volunteers only.
- Port range for your services: see `deploy/README.md`.
