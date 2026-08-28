# Backend (FastAPI modular monolith)

Single Python service. Package boundaries are the architecture; they are not separate deploys.

## Milestone 1

Relational commercial-truth layer: models, Alembic, seed catalogue, catalogue/inventory queries.

Gemini is **not** integrated. `LLMProvider` remains an abstraction; `StubLLMProvider` is still the fallback.

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
| `app/api/` | HTTP adapters |
| `app/core/` | Config, reference IDs, orchestrator (later) |
| `app/db/` | Engine, sessions, Alembic, seed |
| `app/models/` | SQLAlchemy commercial-truth tables |
| `app/schemas/` | Closed vocabularies |
| `app/engines/growth_decision/` | Friction → bounded proposed action (later) |
| `app/engines/policy/` | Deterministic allow / block / approval (later) |
| `app/layers/catalogue/` | Product/SKU/inventory truth |
| `app/layers/basket/` | Versioned baskets and approvals |
| `app/layers/evidence/` | Evidence packs and append-only audit |
| `app/layers/payments/` | Payment provider interface |
| `app/providers/llm/` | `LLMProvider` protocol; Gemini free tier is the MVP live provider (not integrated yet); stub fallback |

AI reasons. This service’s deterministic modules decide whether anything is allowed, executed, or verified.
