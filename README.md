# AdmitAI

Plug-and-play AI admissions platform for universities and colleges — chat, WhatsApp and voice assistants grounded in each college's own knowledge, with role-based handoff to admission officers and faculty, consented lead capture, and a governed self-improvement loop.

This repository is the **Level 1 (MVP)** build: a PG project at the HPAI Lab, built by three students in three modules with a guide. The full specification (PRD) and server notes are kept in the team's private repository; team members can read them at `Hpai-lab1/admitai-internal`.

## Modules and owners

| Folder | Module | Owner |
|---|---|---|
| [`knowledge/`](knowledge/) | **A — Knowledge & Learning** | @adityadk03 |
| [`assistant/`](assistant/) | **B — Assistant Engine & Channels** | @koushikkumar2004 |
| [`experience/`](experience/) | **C — Experience & Operations** | @ramitharavichandra |
| [`contracts/`](contracts/), [`deploy/`](deploy/), `.github/` | **0 — Guide** | @narenccode |
| [`eval-data/`](eval-data/) | Shared test set | Guide + Module A |

Each module's README describes its scope; the PRD's Appendix C maps every requirement to its owner.

## Working agreement

1. **Never push to `main`** — it is protected. Create a branch, open a pull request, get **one teammate's approval**, and pass the checks; then the author merges. The guide is not needed for normal work.
2. **Stay in your folder.** If you must change another module's folder, its owner is asked to review automatically.
3. **The guide is involved only when it matters:**
   - changes to shared or sensitive paths (`contracts/`, `deploy/`, `eval-data/`, `.github/`, `.claude/`, `CLAUDE.md`, `SECURITY.md`, `.gitignore`, `.pre-commit-config.yaml`) need the guide's approval, requested automatically;
   - the **guide-gate** check blocks a PR and alerts the guide if it finds something that looks like a secret, a real phone number or e-mail, an internal IP address, a `.env` file, or a document or data file. Remove it and push again, or ask the guide to clear a false positive.
4. **Modules talk only through the contracts** in `contracts/` — no reading another module's database tables.
5. **This repository is public.** Never commit secrets, real applicant data, server addresses, account names or anything from the private repository.
6. **Every pull request** has a description, links its issue (requirement ID), and includes tests.
7. **Read [`CLAUDE.md`](CLAUDE.md)** before your first Claude Code session, and [`SECURITY.md`](SECURITY.md) once.

Branch names: `a/<short-topic>`, `b/<short-topic>`, `c/<short-topic>`, `guide/<short-topic>`.

The full GitHub and review process — setup, daily flow, how to review, checks and troubleshooting — is in [`docs/REVIEW-PROCESS.md`](docs/REVIEW-PROCESS.md).

## First-time setup (each student)

1. On GitHub: **Settings → Emails →** turn on *Keep my email addresses private* and *Block command line pushes that expose my email*. Commit with your `@users.noreply.github.com` address.
2. Clone the repository, then install the pre-commit hooks (secret scan, lint, no commits to `main`):
   ```bash
   pip install pre-commit && pre-commit install
   ```
3. Copy `.env.example` files to `.env` in your module when they exist; never commit `.env`.

## Timeline

13-week build followed by a 9-week supervised pilot. Milestones: contracts frozen (week 2), walking skeleton (week 4), feature-complete chat + WhatsApp (week 8), full improvement cycle (week 10), code freeze (week 11), go-live readiness (week 13).

## Stack (Level 1, free tier, self-hosted)

Python 3.12 · FastAPI · LangGraph · LiteLLM · Ollama (Qwen2.5-14B-Instruct, Llama-3.1-8B, nomic-embed-text; DeepSeek-R1-Distill for comparison) · PostgreSQL 16 + pgvector (self-hosted Supabase) · Redis · LiveKit · faster-whisper · Piper · Langfuse · React + TypeScript + Next.js + Tailwind · Docker Compose.

## Licence

No licence has been chosen yet, so all rights are reserved for now. Do not reuse this code outside the project until a licence is added.
