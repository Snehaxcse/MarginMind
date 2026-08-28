# MarginMind

Policy-controlled AI merchant-growth decision engine for conversational commerce.

**Understand the hesitation. Earn the conversion. Growth without breaking trust.**

This repository is in **Milestone 0**: architecture and folder contracts only. The application is not implemented yet.

## Read first

1. [Product specification](docs/MarginMind%20—%20Product%20%26%20Build%20Specification.md)
2. [Architecture](docs/ARCHITECTURE.md)
3. [Build plan](docs/BUILD_PLAN.md)

## Intended stack (not installed in M0)

| Layer | Stack |
| --- | --- |
| Frontend | Next.js, TypeScript, Tailwind CSS, shadcn/ui |
| Backend | Python, FastAPI, Pydantic, SQLAlchemy |
| Database | PostgreSQL |
| Payments | Razorpay Test Mode (later) |
| AI | `LLMProvider` abstraction; MVP live provider = Google Gemini API (free tier); `StubLLMProvider` fallback; structured outputs only. Not integrated in M0. |
| Deploy | Vercel (frontend), Render (backend) |

## Layout

```
frontend/   Customer copilot + merchant Growth Control Centre
backend/    FastAPI modular monolith (engines, policy, catalogue, payments)
eval/       Synthetic scenario harness
data/seed/  Demo catalogue and policies
docs/       Spec, architecture, build plan
```

Do not install dependencies or scaffold the Next.js app until the corresponding build-plan milestone is approved.
