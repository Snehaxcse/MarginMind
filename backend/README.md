# Backend (FastAPI modular monolith)

Single Python service. Package boundaries are the architecture; they are not separate deploys.

## Milestone 10

Server-side Razorpay **webhook verification**. Client checkout success is never `VERIFIED`.

Verify `HMAC-SHA256(RAZORPAY_WEBHOOK_SECRET, raw request body bytes)` against `X-Razorpay-Signature` **before** parsing JSON. Use `hmac.compare_digest`. Do not reuse `RAZORPAY_KEY_SECRET` as the webhook secret.

Idempotency: `x-razorpay-event-id` (unique with provider). Duplicate deliveries return 2xx and do not double-apply. SHA-256 of the raw body is stored on `webhook_events` (`WHK-…`); the full payload is not kept.

`payment.captured` / `order.paid` may set Payment + CheckoutAttempt to `VERIFIED` only when `provider_order_id`, `amount_minor`, and `INR` match the CheckoutAttempt. `payment.authorized` is not verified. `VERIFIED` is never downgraded.

HTTP:

```
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
| `app/api/` | HTTP adapters (`POST/GET /api/v1/checkout`, `POST /api/v1/webhooks/razorpay`, `/health`) |
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
