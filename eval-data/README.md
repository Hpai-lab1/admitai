# Evaluation data (owners: guide + Module A)

The shared test set of **real admission questions** collected in week 1 (Discovery), used by all three modules and by the regression gate.

## Rules

- **Anonymised before commit.** Remove names, phone numbers, e-mails, application numbers and anything that identifies a person. Raw source material never enters this repo.
- **30% held out.** The held-out file is used only for final measurement and the regression gate — never for tuning prompts, retrieval or routing.
- Changes go through a pull request reviewed by the guide.

## Format (JSON Lines, one question per line)

```json
{"id": "q-0001", "question": "What is the B.Tech CSE fee for 2026-27?", "intent": "fees", "role": "admission", "department": null, "expected_facts": ["..."], "critical": true, "language": "en", "split": "dev"}
```

`split` is `dev` or `heldout`; `role` is `admission`, `faculty` or `sensitive`.
