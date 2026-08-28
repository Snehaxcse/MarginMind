# Frontend (Next.js)

Customer Shopping Copilot and merchant Growth Control Centre.

**Milestone 0:** directory contract only. Do not scaffold Next.js, install npm packages, or add pages until **M11** (see `docs/BUILD_PLAN.md`).

## Planned app

- `/` — customer copilot (chat, Dress Me, looks, basket, approval, checkout)
- `/merchant` — Growth Control Centre (opportunities, trace, policies, guardrails)

The UI is a client of the FastAPI backend. It captures session signals. It does not compute commercial truth (prices, margin, offers, payment state).
