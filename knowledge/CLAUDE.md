# Module A — Knowledge & Learning (Claude Code notes)

Follow the root `CLAUDE.md` first. This module's scope is in `README.md` here.

- Work only inside `knowledge/`. Branch prefix: `a/`.
- Database schemas you own: `kb` and `learn`. Never write to `chat` or `app`.
- Your API must match `contracts/knowledge.openapi.yaml`. If you think the contract needs to change, stop and write down the proposed change for a pull request to the guide.
- Every number, date and fee you return from `/catalogue/*` is authoritative for Module B's sentence checks — never round or reformat values.
- Curated answers always carry `academic_year` and `valid_to`; the catalogue wins any conflict with them.
- Documents for tests: synthetic, or public college web pages used with permission. No real enquiries outside the anonymised `eval-data/` set.
- Embeddings: `nomic-embed-text` (768 dimensions) via the shared Ollama service. Grading runs use the DeepSeek distills only in the scheduled window (`deploy/README.md`).
- Port range for your services: see `deploy/README.md`.
