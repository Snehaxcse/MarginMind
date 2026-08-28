# Backend (FastAPI modular monolith)

Single Python service. Package boundaries are the architecture; they are not separate deploys.

## Milestone 9

Server-side Razorpay **Test Mode** order creation and a deterministic checkout state machine. A Razorpay order is created only after granted exact-version approval **and** M8 revalidation `PASS`. Amounts are integer paise from catalogue truth (₹2,447 → `244700`). Caller-supplied prices are rejected. Client “payment succeeded” is stored as `PAYMENT_REPORTED` / `VERIFICATION_PENDING` only — never `VERIFIED`. Webhook/signature verification is M10.

`PAYMENT_PROVIDER=stub` (default) or `razorpay`. Keys: `RAZORPAY_KEY_ID`, `RAZORPAY_KEY_SECRET`. Automated tests inject `StubPaymentProvider` and do not need credentials. Optional live Test Mode: `MARGINMIND_LIVE_RAZORPAY=1`.

HTTP:

```
POST /api/v1/checkout     { session_ref_id, approval_ref_id }  # no amount
GET  /api/v1/checkout/{CHK-…}
GET  /health
```

Idempotency key: `checkout:{session_ref}:{basket_ref}:v{version}:{approval_ref}`. Repeated requests reuse one provider order. Revalidation failure does not occupy the key. `FAILED` provider attempts are retried in place.

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
| `app/api/` | HTTP adapters (`POST/GET /api/v1/checkout`, `/health`) |
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
| `app/layers/checkout/` | CheckoutAttempt state machine; no verified payment |
| `app/layers/evidence/` | Evidence packs and append-only audit |
| `app/layers/payments/` | `PaymentProvider`; Stub + Razorpay Test Mode |
| `app/providers/llm/` | `LLMProvider` protocol; Gemini free tier is the MVP live provider (not integrated yet); stub fallback |

AI reasons. This service’s deterministic modules decide whether anything is allowed, executed, or verified.
