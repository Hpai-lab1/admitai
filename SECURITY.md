# Security policy

## Reporting a vulnerability

Please **do not open a public issue** for security problems. Use GitHub's private reporting instead: **Security → Report a vulnerability** on this repository. The project guide will respond within a few working days.

## What must never appear in this repository

This repository is public. The following are never committed, pasted into issues or pull requests, or included in screenshots:

- passwords, API keys, tokens, private keys or `.env` files;
- real applicant, parent or student data (names, phone numbers, e-mails, conversations, application numbers);
- server IP addresses, hostnames, account names or infrastructure details beyond `deploy/README.md`.

## Automatic checks

- **Pre-commit hooks** (gitleaks secret scan, private-key detection, no commits to `main`) run on every contributor's machine.
- **GitHub secret scanning with push protection** blocks pushes containing known secret formats.
- **CI** re-runs the secret scan and lint on every pull request, on GitHub-hosted runners only.
- **Dependabot** and **CodeQL** flag vulnerable dependencies and risky code.

## If a secret is committed by mistake

1. Tell the guide immediately.
2. **Revoke and rotate the secret first** — removing the commit is not enough, because public commits are copied within minutes.
3. Then remove it from the code in a new pull request.
