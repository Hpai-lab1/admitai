# Module C — Experience & Operations (Claude Code notes)

Follow the root `CLAUDE.md` first. This module's scope is in `README.md` here.

- Work only inside `experience/`. Branch prefix: `c/`.
- Database schema you own: `app`. Never write to `kb`, `learn` or `chat`.
- Your API must match `contracts/experience.openapi.yaml`. If you think the contract needs to change, stop and write down the proposed change for a pull request to the guide.
- Until the week-4 walking skeleton, build against Module A's and B's mocks, not their real services.
- Row-Level Security is mandatory on every `app` table; test that one tenant can never read another tenant's rows.
- For under-18 enquirers, never store the student's own contact details; a lead exists only after guardian verification (PRD F5.3).
- UI must meet WCAG 2.2 AA (PRD 13): keyboard access, contrast, captions for voice, ARIA live regions for streamed answers.
- Port range for your services: see `deploy/README.md`.
