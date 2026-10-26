# Module A — Knowledge & Learning

**Owner:** @adityadk03 · **Database schemas:** `kb`, `learn` · **PRD:** sections 8, 9.4, 17, 21.2, Appendix C

**Mission:** make the assistant know the right, current facts for each college, and learn from its mistakes under human control.

## Scope (Level 1)

- **Knowledge base (F4.1–F4.8):** upload and OCR, website crawl, program catalogue import, curated Q&A backend, metadata, approval and expiry, chunking and embeddings, hybrid search with reranking, versioning and rollback.
- **Currency rules (8.3, 17):** academic year and expiry on curated answers, catalogue-wins conflict check, 90-day unused flag, ownership registry, weekly KB health report, academic-year cut-over automation.
- **Tenant Pack backend (F10.1–F10.3, F10.6):** import, validation, readiness checklist, launch test set, content readiness review.
- **Agents:** Knowledge Ingestion Agent; Feedback Analyst Agent (nightly clustering, root cause incl. "correct but unwelcome", impact score, proposal drafting, approval SLA and escalation); Evaluation Agent (test-set management, DeepSeek-distill grading, regression gate, model-comparison harness with Module B).
- **Also:** data for the weekly gap report (F8.5); nightly export of top curated answers for degraded mode.

## Contracts

- **Provides:** `/kb/search`, `/catalogue/*`, `/curated-qa`, `/kb/sources`, `/kb/documents`, `/tenant-pack`, `/improvements`, `/evals/runs` — see `contracts/knowledge.openapi.yaml`.
- **Consumes:** Module B's chat API (to run tests headless); Module C's `feedback.created` and `correction.submitted` events.
- **By week 2:** a mock `/kb/search` over a fixture corpus so Module B can build against it.

## Thesis angle

Dense vs hybrid vs reranked retrieval on real admission questions; measured accuracy gain from correction cycles.
