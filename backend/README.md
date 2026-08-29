# Backend (FastAPI modular monolith)

Single Python service. Package boundaries are the architecture; they are not separate deploys.

## Milestone 11

Read-only **Agent Trace** reconstruction. Turns persisted session, evidence, friction, action, policy, approval, revalidation, checkout, payment, webhook, and audit rows into one frontend-friendly `AgentTrace`. Does not execute actions, change payment state, or add UI.

`build_agent_trace(db, session_ref_id)` must work after process restart. Timeline order is `timestamp`, then a stable type rank, then `ref_id`. Events are not fabricated: if catalogue retrieval was never persisted, it does not appear.

Merchant vs customer:

- `GET /api/v1/sessions/{SES-…}/trace` — full merchant view (policy checks, evidence refs, guardrails, `margin_percent`).
- `GET /api/v1/sessions/{SES-…}/progress` — customer-safe progress (no margin, no forgone revenue, no guardrail block). Checkout / client-reported / server-verified payment stages stay distinct.

Guardrail counts are derived from executed commercial state. A `NO_UPSELL` proposal with reason `HARD_BUDGET_VIOLATION` does **not** increment `hard_budget_violation_count`. Final outcome `PAYMENT_VERIFIED` wins over an earlier unverified client callback.

HTTP:

```
GET  /api/v1/sessions/{SES-…}/trace
GET  /api/v1/sessions/{SES-…}/progress
POST /api/v1/webhooks/razorpay   raw body + X-Razorpay-Signature + x-razorpay-event-id
POST /api/v1/checkout            { session_ref_id, approval_ref_id }
GET  /api/v1/checkout/{CHK-…}
POST /api/v1/checkout/{CHK-…}/client-result   # reported only, never VERIFIED
GET  /health
```

## Local setup

From the repository root:

```powershell
copy .env.example .env
docker compose up -d
cd backend
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
alembic upgrade head
python -m app.db.seed
python -m app.db.seed
pytest
```

If Docker is not installed, `python scripts/verify_m1.py` can start a workspace-local Postgres under `backend/.pgdata` (requires `pip install pgserver`; not a host service install). Canonical development remains docker-compose.

Seed is idempotent. Running `python -m app.db.seed` again must not duplicate merchants, SKUs, policies, or offers.

### Reset development data

Option A — truncate then re-seed (keeps the database volume):

```powershell
python -m app.db.seed --reset
```

Option B — destroy the Postgres volume:

```powershell
docker compose down -v
docker compose up -d
alembic upgrade head
python -m app.db.seed
```

## Packages

| Path | Responsibility |
| --- | --- |
| `app/api/` | HTTP adapters (`POST/GET /api/v1/checkout`, webhooks, `GET .../trace` and `.../progress`, `/health`) |
| `app/core/` | Config, reference IDs, orchestrator (later) |
| `app/db/` | Engine, sessions, Alembic, seed |
| `app/models/` | SQLAlchemy commercial-truth tables |
| `app/schemas/` | Closed vocabularies |
| `app/engines/growth_decision/` | Friction → bounded proposed action |
| `app/engines/policy/` | Deterministic allow / block / approval |
| `app/layers/catalogue/` | Product/SKU/inventory truth |
| `app/layers/basket/` | Versioned baskets |
| `app/layers/approval/` | Exact-version grant; grant ≠ execute |
| `app/layers/revalidation/` | Final live re-check; approval ≠ success |
| `app/layers/checkout/` | CheckoutAttempt state machine; webhook apply |
| `app/layers/trace/` | Read-only Agent Trace reconstruction |
| `app/layers/evidence/` | Evidence packs and append-only audit |
| `app/layers/payments/` | `PaymentProvider`; Stub + Razorpay Test Mode |
| `app/providers/llm/` | `LLMProvider` protocol; Gemini free tier is the MVP live provider (not integrated yet); stub fallback |

AI reasons. This service’s deterministic modules decide whether anything is allowed, executed, or verified.
