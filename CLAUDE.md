# AdmitAI — rules for every Claude Code session

You are working in the AdmitAI monorepo. **This repository is public.** The module you are working on has its own `CLAUDE.md` and `README.md`; read them before making changes. The full specification lives in the team's private repository, which the student can open — never copy its contents into this repository.

## Hard rules (never break these)

- **Stay inside your module's folder.** Do not edit another module's folder, `contracts/`, `deploy/` or `.github/`. If a change there is needed, stop and describe it so the student can raise a pull request.
- **Never push to `main`** and never force-push. Work on a branch (`a/…`, `b/…`, `c/…`); merging happens through a reviewed pull request.
- **Nothing private in this repository:** no secrets or keys, no real applicant data (names, phone numbers, e-mails, conversations), no server IP addresses, hostnames, account names or port details beyond those in `deploy/README.md`. Keys and passwords go in a local `.env` (git-ignored); commit only `.env.example` with placeholders.
- **Do not bypass the safety checks:** no `git commit --no-verify`, no disabling pre-commit hooks, no editing `.claude/settings.json` to loosen permissions.

## Lab server rules

- The lab servers are **shared** and run other tenants' services. Never stop, restart, modify or remove containers you did not start, and never bind to ports outside your module's range in `deploy/README.md`.
- **No system changes:** no `sudo`, no package installs or removals, no driver, kernel or Docker daemon changes.
- **GPUs:** use only the shared model service; never load a model onto a GPU yourself, and never run one workload across both GPUs.
- Bind every service to `127.0.0.1` on the project's Docker network; only the guide exposes anything outside the server.
- The detailed server notes are in the private repository; follow them when working on the servers.

## Engineering conventions

- Python 3.12, FastAPI, Pydantic v2, `pytest`; format with `ruff format`, lint with `ruff check`.
- TypeScript (strict), React, Next.js, Tailwind; `vitest` for unit tests; `eslint` + `prettier`.
- Every API a module provides must match its OpenAPI file in `contracts/`. If they differ, the contract wins.
- Database: each module writes migrations only for its own schema (`kb`/`learn` for A, `chat` for B, `app` for C). Cross-module data goes through APIs, never direct table access.
- Every change ships with tests. A pull request is done when tests and CI pass, the API matches its contract, and the README is updated if behaviour changed.
- Prefer small, focused pull requests linked to one requirement ID from the PRD's Appendix C.
