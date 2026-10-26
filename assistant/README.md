# Module B — Assistant Engine & Channels

**Owner:** to be added · **Database schema:** `chat` · **PRD:** sections 7, 9.2–9.5, 21.2, Appendix C

**Mission:** turn a question on any channel into a fast, grounded, safe answer — or the right handoff.

## Scope (Level 1)

- **Model layer:** LiteLLM + Ollama configuration, model policies per tenant, latency budget (PRD 7.5) and benchmarks.
- **Conversation engine (F1.2–F1.7 engine side):** router with rules and 8B fallback, multi-intent (F3.1, F3.1a); Answer agent with Admission and Faculty role packs (7.1–7.3); sentence-buffered streaming with deterministic checks (F1.4, F1.4a); asynchronous grounding review (F1.4b); input guardrails and PII masking.
- **Channels:** WhatsApp text (F1.11, F1.12); browser voice pipeline — LiveKit worker, faster-whisper, Piper, VAD, barge-in, voice style, digit-by-digit confirmation, channel consistency (F2.1–F2.6, F2.9, F2.10).
- **Decisions:** handoff package (F3.3) and callback creation through Module C; conversational lead capture, minor detection and scoring rules (F5.1, F5.3 detection, F5.4, F5.6).
- **Comparison:** DeepSeek-R1-Distill vs Qwen/Llama on the held-out test set (accuracy, groundedness, latency, GPU memory).

## Contracts

- **Provides:** `/chat/{id}/messages` (stream), `/voice/token`, `/channels/whatsapp/webhook`, conversation events — see `contracts/assistant.openapi.yaml`.
- **Consumes:** Module A's `/kb/search` and `/catalogue/*`; Module C's `/handoffs`, `/consents`, `/leads`.
- **By week 2:** a mock chat stream so Module C can build the widget against it.

## Thesis angle

Hallucination control with deterministic sentence checks; local SLMs vs DeepSeek distilled models under a latency budget.
