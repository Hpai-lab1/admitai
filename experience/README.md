# Module C — Experience & Operations

**Owner:** @ramitharavichandra · **Database schema:** `app` · **PRD:** sections 5, 13, 18, 21.2, Appendix C

**Mission:** everything people see and operate, with identity, consent and audit done properly.

## Scope (Level 1)

- **Identity and governance:** sign-in, roles, MFA, break-glass, Row-Level Security policies, audit log (PRD 5.4, 11.3).
- **Web widget:** chat UI (F1.1, F1.5, F1.7, F1.8), voice controls, orb and captions (F2), WCAG 2.2 AA (13).
- **Staff console (F7.1–F7.5, F7.7):** handoff and callback queues (F3.2, F3.4, F3.6), notifications, conversation view, corrections, impact-sorted approval inbox with batch approval.
- **Consent and leads:** consent records, guardian verification by e-mail or WhatsApp code, withdrawal, lead list, pipeline and export (F5.2, F5.3 verification, F5.5, F6.7).
- **Feedback (F8.1–F8.4):** capture on web and WhatsApp; events to Module A.
- **Admin portal:** tenant configuration, users and roles, knowledge-base screens backed by Module A, Tenant Pack upload and readiness checklist, sandbox preview (F10.4).
- **Analytics (F9.1–F9.4):** overview, topics, quality and platform dashboards.

## Contracts

- **Provides:** `/auth`, `/handoffs`, `/callbacks`, `/leads`, `/consents`, `/feedback`, `/corrections`, `/analytics`, admin APIs — see `contracts/experience.openapi.yaml`.
- **Consumes:** Module A's KB, curated-Q&A, improvement and eval APIs; Module B's chat and voice APIs.
- **Until week 4:** build against Module A's and B's mocks.

## Thesis angle

Staff usability study of the console and approval workflow; consent and accessibility UX for parents.
