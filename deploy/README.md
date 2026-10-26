# Deployment (owner: guide)

Docker Compose files for the build environment (weeks 1–13) and the pilot (weeks 14–22). Server-specific settings are supplied through git-ignored `.env` files; nothing environment-specific is committed here.

## Port ranges for AdmitAI services (bound to 127.0.0.1)

| Range | Use |
|---|---|
| 8100–8149 | Module A services |
| 8150–8199 | Module B services |
| 8200–8249 | Module C services |
| 8300–8349 | Shared services (Langfuse, model gateway) |
| 5440 | Shared PostgreSQL (schemas `kb`, `learn`, `chat`, `app`) |
| 6390 | Shared Redis |

Use only these. Other ports on the lab servers belong to other services.

## Shared model service

One Ollama instance, managed by the guide, serving Qwen2.5-14B-Instruct, Llama-3.1-8B, nomic-embed-text, faster-whisper and a reranker. DeepSeek-R1-Distill models for comparison run only in a scheduled window. Modules reach it through the LiteLLM gateway; they never load models themselves.

## Access

Student accounts on the servers have no `sudo`. Containers are started through compose scripts the guide maintains. Only the guide exposes services outside the server.

## CI

GitHub Actions run on GitHub-hosted runners only. Lab servers are never registered as runners for this repository.
