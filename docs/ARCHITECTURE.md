# MarginMind Architecture

**Milestone:** 9 — Razorpay Test Mode order + checkout state machine  
**Status:** M0–M9 implemented. Client checkout success is not verified payment.  
**Authoritative product spec:** [MarginMind — Product & Build Specification.md](./MarginMind%20—%20Product%20%26%20Build%20Specification.md)

This document describes a **5-day Buildathon architecture**. It optimises for a reliable live demo of the locked MVP, not for a production multi-service platform.

---

## 1. Product in one sentence

MarginMind is a **policy-controlled AI merchant-growth decision engine**. The customer sees a shopping copilot. The merchant sees why shoppers hesitate, what the agent proposed, whether policy allowed it, whether the customer approved it, whether commerce state still held, and whether payment actually succeeded.

The system is **not** a generic fashion recommendation chatbot.

---

## 2. Non-negotiable design rule

```
AI reasons over ambiguity.
Deterministic code establishes commercial truth.
Policies control actions.
Approval preserves agency.
Revalidation prevents stale execution.
Verification establishes outcomes.
Evidence makes decisions reconstructable.
```

| AI may produce | Code alone may produce |
| --- | --- |
| Intent, style, friction hypothesis, ranking among real SKUs, copy, What/Why/Fix narrative | SKU existence, price, stock, size, hard budget, basket total, margin, offer eligibility, approval state, checkout state, Razorpay order, payment state |

The LLM never: invents SKUs, sets prices, applies offers, charges the customer, overrides policy, or handles payment credentials.

---

## 3. Hackathon architecture choice: modular monolith

**One FastAPI process. One PostgreSQL database. One Next.js app with two surfaces.**

| Option | Why rejected / accepted |
| --- | --- |
| Microservices (policy, catalogue, payments as separate deploys) | Ops cost too high for 5 days; same database anyway |
| Next.js full-stack with Python as a sidecar only | Policy, eval, and webhooks belong in Python; keep commercial truth in one backend |
| **Modular monolith (chosen)** | Clear package boundaries, one Render service, in-process eval, still tells a strong architecture story |

Deploy targets remain as specified:

- Frontend → Vercel
- Backend → Render
- Database → PostgreSQL (Render or local Docker)

**MVP LLM:** Google Gemini API (free tier), behind the `LLMProvider` abstraction. `StubLLMProvider` is the deterministic fallback. Gemini is **not** integrated in Milestone 0.

---

## 4. System diagram

```
┌──────────────────────────────────────────────────────────────────┐
│ Next.js + TypeScript + Tailwind + shadcn/ui          (Vercel)    │
│                                                                  │
│  Customer Copilot                    Merchant Growth Control     │
│  - conversation                      - top opportunities         │
│  - Dress Me / looks                  - What / Why / Fix          │
│  - fit preferences                   - agent trace               │
│  - basket + approval                 - policy studio             │
│  - checkout widget                   - guardrail / audit         │
│  - session-signal capture                                        │
└──────────────────────────────┬───────────────────────────────────┘
                               │ HTTPS JSON
                               ▼
┌──────────────────────────────────────────────────────────────────┐
│ FastAPI + Pydantic + SQLAlchemy                      (Render)    │
│                                                                  │
│  HTTP routers (thin)  →  Orchestrator (session pipeline)         │
│                                                                  │
│  ┌─────────────┐  ┌──────────────┐  ┌─────────────────────────┐  │
│  │ AI Reasoning│  │ Growth       │  │ Policy Engine           │  │
│  │ Layer       │  │ Decision     │  │ deterministic           │  │
│  │ structured  │  │ Engine       │  │ PASS / BLOCK / APPROVAL │  │
│  │ outputs only│  │ friction →   │  │                         │  │
│  │             │  │ bounded act  │  │                         │  │
│  └──────┬──────┘  └──────┬───────┘  └────────────┬────────────┘  │
│         │                │                       │               │
│  ┌──────▼──────┐  ┌──────▼───────┐  ┌────────────▼────────────┐  │
│  │ Catalogue / │  │ Basket /     │  │ Razorpay / Payments     │  │
│  │ Inventory   │  │ Approval     │  │ interface now; test     │  │
│  │             │  │              │  │ mode later              │  │
│  └─────────────┘  └──────────────┘  └─────────────────────────┘  │
│                                                                  │
│  Evidence Store + append-only Audit / Agent Trace                │
│  PostgreSQL                                                      │
└──────────────────────────────────────────────────────────────────┘
                               │
                               ▼
┌──────────────────────────────────────────────────────────────────┐
│ eval/  in-process synthetic harness (50–100 labelled scenarios)  │
└──────────────────────────────────────────────────────────────────┘
```

---

## 5. Locked MVP modules only

From the spec §62. Stretch features (Get the Vibe, colour guidance, Couple Mode, Demand Gap, campaigns, experiments, bulk merchant actions) are **out of scope** until the pipeline below works.

| # | Module | Role in MVP |
| --- | --- | --- |
| 1 | Conversational intent | Structured intent object from customer message |
| 2 | Dress Me | Few clarifying questions → 3 complete looks, not 30 SKUs |
| 3 | Fit/comfort preferences | Explicit customer constraints/preferences; never inferred body commentary |
| 4 | Product recommendations | Real catalogue SKUs after hard filters |
| 5 | Basket Architect | Complete the look inside hard budget |
| 6 | Conversion Friction Resolver | Classify hesitation; choose smallest intervention |
| 7 | Evidence-backed diagnosis | What / Why / Fix + evidence IDs |
| 8 | Bounded action vocabulary | Closed set including `NO_UPSELL` and `STOP` |
| 9 | Policy Engine | Merchant + customer hard rules; autonomy levels |
| 10 | Growth Control Centre | Merchant sees opportunities, traces, guardrails |
| 11 | Agent Trace | Reconstructable session timeline |
| 12 | Inventory/price/offer revalidation | Approval ≠ success |
| 13 | Customer approval | Exact basket version |
| 14 | Razorpay test checkout | After revalidation |
| 15 | Payment verification + idempotent webhooks | Independent outcome |
| 16 | Synthetic evaluation | 50–100 labelled scenarios; safety metrics |

---

## 6. Component responsibilities

### 6.1 Frontend (`frontend/`)

**Does**

- Render customer copilot and merchant control centre.
- Send messages, preference updates, and **session signals** (product views, size-guide opens, rejections, checkout-started).
- Display looks, baskets, policy-safe copy, and approval prompts returned by the API.
- Host Razorpay Checkout.js later, using a server-created order id only.
- Show merchant traces and guardrail counts from stored events.

**Does not**

- Compute source-of-truth basket totals, margins, or offer eligibility.
- Apply discounts locally.
- Call the LLM.
- Trust the client as proof of payment.

Two route groups, one app:

- `/` customer copilot
- `/merchant` growth control centre

### 6.2 Backend API (`backend/app/api/`)

Thin HTTP adapters. Authenticate a **demo customer** and **demo merchant**. Orchestrate the pipeline. Persist state. Expose webhook endpoint.

Proposed MVP routes:

| Method | Path | Purpose |
| --- | --- | --- |
| POST | `/api/v1/sessions` | Open shopping session |
| POST | `/api/v1/sessions/{id}/messages` | Customer utterance → pipeline |
| POST | `/api/v1/sessions/{id}/signals` | UI behavioural evidence |
| GET | `/api/v1/sessions/{id}` | Session snapshot for UI |
| POST | `/api/v1/baskets/{id}/approve` | Bind approval to basket version |
| POST | `/api/v1/checkout/attempts` | Revalidate + create payment order |
| POST | `/api/v1/webhooks/razorpay` | Signature verify + idempotent capture |
| GET | `/api/v1/merchant/opportunities` | Top growth opportunities |
| GET | `/api/v1/merchant/sessions/{id}/trace` | Agent trace |
| GET/PUT | `/api/v1/merchant/policies` | Policy studio |
| GET | `/api/v1/merchant/guardrails` | Safety counters |
| GET | `/api/v1/merchant/audit` | Searchable audit |

### 6.3 AI reasoning layer (`backend/app/providers/llm/`)

Provider abstraction. **No vendor SDK in Milestone 0.**

Responsibilities:

- Intent extraction → `Intent` schema.
- Optional ranking among **already filtered** candidate SKUs.
- Conversational wording.
- Friction hypothesis **only when rules are insufficient**, always with confidence.
- What / Why / Fix text generated from evidence records, not from invented facts.

Interface (conceptual):

```
complete_structured(schema_name, instructions, input_payload) -> dict
```

The orchestrator **validates** that dict with Pydantic. Invalid or schema-violating output → `STOP` / ask, never a commercial action.

M3 implements `StubLLMProvider` for `schema_name=intent_extraction` only. Gemini is still not integrated.

Implementations (swap via config, e.g. `LLM_PROVIDER=gemini|stub`):

1. `StubLLMProvider` — deterministic fixtures for demo, eval, and outage fallback. **Build first.**
2. `GeminiLLMProvider` — **MVP live provider.** Google Gemini API, free tier. Structured outputs only. **Do not integrate in Milestone 0.** Wire only after the stub path is green.

Keep the `LLMProvider` protocol regardless of which implementation is active. Neither provider is a source of prices, inventory, SKUs, or policy outcomes.

### 6.4 Growth Decision Engine (`backend/app/engines/growth_decision/`)

The hero system. It **proposes**; it does not execute.

Inputs: intent, hard/soft preferences, session signals, catalogue candidates, current basket, merchant goal, offers (as data, not as permission).

- Outputs a `ProposedAction` (`ACT-001`) with WHAT / WHY / FIX. Status is **PROPOSED**. `requires_policy_check` is always true. The GDE does not authorize or execute.
- Friction mapping: fit/colour → `GUIDE_CONFIDENCE`; style → `BUILD_BASKET` / `RECOMMEND`; budget → rescue then `REBUILD_BASKET`; price → cheaper SKU before any offer; OOS/size → `FIND_ALTERNATIVE`; overload → `SIMPLIFY_CHOICES` (≤3); incomplete → `BUILD_BASKET`; gap/unknown/none → `STOP`; attach past HARD budget → `NO_UPSELL`.
- Rescue hierarchy: cheaper equivalent → rebuild look → remove optional accessory → seeded offer (price hesitation only, never invented) → `NO_UPSELL` / `STOP`.
- Does not mutate baskets, inventory, or approvals.

**Demo reliability decision:** classify obvious friction with deterministic rules from signals (size-guide count, budget vs basket, OOS, hard-budget attach). Use the LLM only to fill structured intent and copy. Judges must see the pipeline even if the model is down.

### 6.5 Policy Engine (`backend/app/engines/policy/`)

Separate module. Pure validation over **database-backed** commercial context.

**Proposal ≠ permission.** The Growth Decision Engine may emit a `ProposedAction`. Only `validate_action` decides whether that action is allowed. The Policy Engine never proposes, never executes checkout, never mutates baskets, never applies offers, and never trusts GDE-supplied totals.

```
validate_action(db, session, proposal) -> PolicyDecision   # PDEC-001
```

Closed overall verdicts:

| Verdict | `allowed` | Meaning |
| --- | --- | --- |
| `PASS` | true | Allowed to show or, if already granted for this exact version, allowed in principle |
| `BLOCK` | false | Commercial truth failed. Must not execute |
| `APPROVAL_REQUIRED` | true | Allowed in principle, but must not execute until the customer approves this action + exact basket version |

Per-check status is `PASS` / `FAIL` / `N/A`. Any commercial `FAIL` → overall `BLOCK`. A missing customer approval is not a commercial fail: overall `APPROVAL_REQUIRED`.

Checks (always recorded):

`HARD_BUDGET` · `INVENTORY` · `SKU_EXISTS` · `PRODUCT_ACTIVE` · `VARIANT_ACTIVE` · `MARGIN` · `AUTHORIZED_OFFER` · `OFFER_ACTIVE` · `OFFER_ELIGIBILITY` · `OFFER_STACKING` · `MERCHANT_RESTRICTIONS` · `CUSTOMER_APPROVAL_REQUIRED` · `NO_SILENT_BASKET_CHANGE`

Seeded merchant policies (`POL-001`–`POL-008`) are interpreted from typed fields, not description text. HARD budget and real inventory are fail-closed even if a row is missing.

Action-specific behaviour:

| Action | Typical verdict | Notes |
| --- | --- | --- |
| `GUIDE_CONFIDENCE` | PASS | No financial checks; no approval to show guidance |
| `SIMPLIFY_CHOICES` / `RECOMMEND` / `FIND_ALTERNATIVE` | PASS if SKUs real | Showing is not replacing |
| `NO_UPSELL` / `STOP` | PASS | Not blocked because revenue is skipped |
| `BUILD_BASKET` / `REBUILD_BASKET` | APPROVAL_REQUIRED if valid | Recalculate `sum(effective_price * qty)` vs HARD budget |
| `APPLY_AUTHORIZED_OFFER` | APPROVAL_REQUIRED if valid, else BLOCK | Unknown/inactive/ineligible/stacking/margin/discount → BLOCK. Never substitute another offer |
| `REQUEST_CHECKOUT` | APPROVAL_REQUIRED until exact-version grant | Does not execute checkout |

**HARD budget:** for any action that would create/change a basket, if shopper budget is HARD, resulting total must be `<=` budget. Totals are recomputed from catalogue `effective_price`. Example: GDE candidates `SKU-004-M` + `SKU-007-M` + `SKU-012-OS` = ₹2,647 vs HARD ₹2,500 → `BLOCK` / `HARD_BUDGET_VIOLATION`.

**Margin:** `cost = price * (1 - margin_percent/100)`. After discount, `margin = (discounted - cost) / discounted * 100`. Floor is `max(POL-006, offer.min_margin_percent)` when an offer applies. Below floor → `MARGIN_FLOOR_VIOLATION`. OFR-001 at 10% on trousers (`SKU-004-M`, 35% seeded margin) falls to ~27.8% and is blocked.

**Offers:** an M6 offer candidate is only a suggestion. Policy independently verifies `OFR-` existence, merchant ownership, active window, basket minimum, eligible SKU/category, `POL-007` max discount, margin floor, and `POL-008` stacking. Unknown offer → BLOCK. No silent substitution.

The LLM cannot override this object.

Autonomy (spec §24):

| Level | Examples |
| --- | --- |
| AUTO | Rank, explain fit, simplify to 3, comparison copy (`GUIDE_CONFIDENCE`, `SIMPLIFY_CHOICES`, `NO_UPSELL`, `STOP`) |
| APPROVAL_REQUIRED | Replace basket item, apply offer, change basket, request checkout |
| NEVER AUTONOMOUS | Invent discount, override hard budget, change policy, charge, silent basket mutation, invent SKU, override margin |

### 6.6 Catalogue / inventory (`backend/app/layers/catalogue/`)

Source of product truth.

- Products + variants + stock + fashion metadata (fit, silhouette, length, occasion/style tags, margin band).
- Retrieval pipeline: **hard filters first** via `CatalogueConstraints` (budget, size, in-stock, merchant, restricted SKUs/products, excluded material/coverage/fit/silhouette, inactive rows). Unknown SKU / unknown merchant / malformed tokens fail closed.
- A HARD customer budget may be copied to `max_price` as a **per-item candidate ceiling only**.
- **Basket-total** HARD budget is enforced by the basket layer: `sum(effective_price * qty)` must be `<=` budget. Individually valid SKUs can still fail as a set.
- `SoftCatalogueSignals` (colour, silhouette, fit, style, occasion) are ranking hints only and **never exclude** a candidate.
- One-size variants (`OS`) stay eligible under `required_size` unless `allow_one_size=False`.
- Revalidation reads live stock and price, not the recommendation cache.
- The model cannot insert SKUs.

Seeded synthetic catalogue is acceptable and expected for the demo.

### 6.7 Basket / approval (`backend/app/layers/basket/`, `backend/app/layers/approval/`)

- Basket is **versioned** (`BASK-001@v1`). Material changes copy-on-write to a new version; prior versions stay reconstructable.
- Line prices are catalogue effective prices (override or base). Callers cannot supply the authoritative price.
- `validate_basket` checks inventory plus **total** HARD budget. FLEXIBLE overage warns; unknown budget skips the cap.
- `build_complete_looks` scores structured metadata only (occasion/fit/style/colour + composition). No AI ranking, no discounts.
- `evaluate_optional_add_on` is the NO_UPSELL foundation (`HARD_BUDGET_VIOLATION`). It does not run the Growth Decision Engine.
- `propose_replacement` evaluates a swap without mutating the basket.
- Approval (`APR-001`) binds to **session + action + exact basket row/version**. Granting approval does not execute. `BASK-001@v1` never authorizes `BASK-001@v2`. A rebuild/replacement requires a new approval. Granting sets the basket to `APPROVED_UNVERIFIED` (still not checkout).

### 6.7.1 Final revalidation (`backend/app/layers/revalidation/`)

**Approval ≠ success.** A granted `APR-001` freezes `BASK-001@v1`. It does not authorize execution against later catalogue, inventory, offer, or price state.

```
revalidate_approved_basket(db, session, approval_ref_id) -> RevalidationResult   # REVAL-001
```

Closed statuses: `PASS` · `FAILED` · `STOPPED`. Per-check status is `PASS` / `FAIL` / `N/A`.

Checks (always recorded): `SKU_EXISTS` · `PRODUCT_ACTIVE` · `VARIANT_ACTIVE` · `CORRECT_VARIANT` · `INVENTORY_AVAILABLE` · `QUANTITY_AVAILABLE` · `PRICE_UNCHANGED` · `HARD_BUDGET` · `OFFER_EXISTS` · `OFFER_ACTIVE` · `OFFER_ELIGIBILITY` · `MARGIN_VALID` · `MERCHANT_POLICY_VALID` · `CUSTOMER_APPROVAL_VALID` · `BASKET_VERSION_VALID`

Exact-version rule: `APR-001` may only revalidate `BASK-001@v1`. If the workflow basket is `@v2`, result is `STOPPED` with `STALE_APPROVAL` / `BASKET_VERSION_MISMATCH`. Approvals never carry forward.

Price change vs snapshot → `PRICE_CHANGED` (`FAILED`). The approved lines are not rewritten. OOS on an approved SKU → `FAILED` / `OUT_OF_STOCK` (hero failure). Invalid/expired offer on the approved plan → `STOPPED`; the offer is not silently dropped.

Policy Engine is **re-invoked against current state**. A PolicyDecision from five minutes ago is not reused as truth.

OOS rescue (`propose_oos_replacement`) returns a candidate only: real SKU, active, in stock, hard constraints, HARD total, merchant margin. Soft fit/style scores rank; Gemini is not used. Accepting forks `BASK-001@v2` via copy-on-write. v1 stays reconstructable and keeps `APR-001`. v2 gets a **new** pending approval. Rejecting leaves v1 unchanged and checkout stopped.

Repeated revalidation against unchanged commercial state returns the same `REVAL` row (fingerprint on approval + live stock/price/offer/budget). It does not spawn replacement baskets.

M8 does not create Razorpay orders or decrement inventory at checkout. M9 may create a Test Mode order only after this revalidation returns PASS.

### 6.7.2 Conversion friction (`backend/app/layers/friction/`)

Rule-first diagnosis from session signals + basket/inventory truth. Gemini is not used. The Growth Decision Engine is not invoked.

- `record_session_signal` writes a `session_events` row (`EVT-…`) and an evidence row (`EVD-…`).
- `diagnose_friction` returns a ranked `FrictionEvaluation` (primary + secondary). WHAT/WHY only; no FIX and no `BoundedAction`.
- Confidence is a documented step function: 1 signal → 0.45, 2 → 0.65, 3 → 0.84, 4+ or commercial-truth rules → 0.92.
- Insufficient evidence yields `NONE` (quiet session) or `UNKNOWN` (activity without a matching rule). Diagnoses always carry `evidence_ref_ids`.
- Persisted as `friction_diagnoses` (`FRIC-001`).

### 6.8 Razorpay / payments (`backend/app/layers/payments/`, `backend/app/layers/checkout/`)

Application code depends on `PaymentProvider`, not on Razorpay SDK or HTTP details.

```
PaymentProvider.create_order(amount_minor, currency, receipt, notes, idempotency_key) -> PaymentOrder
PaymentProvider.verify_webhook(payload, signature) -> PaymentEvent   # M10; raises not_implemented in M9
```

Implementations:

- `StubPaymentProvider` — deterministic fake `order_stub_{CHK-…}` IDs. Preserves amount/currency. `fail=True` simulates provider outage. Used by automated tests. Never hits the network.
- `RazorpayPaymentProvider` — Razorpay **Test Mode** via `httpx` (`POST https://api.razorpay.com/v1/orders`). Auth is `RAZORPAY_KEY_ID` / `RAZORPAY_KEY_SECRET` from the environment. The secret is never logged, persisted in code, or returned to clients.

`PAYMENT_PROVIDER=stub|razorpay` selects the live process default. Tests inject `StubPaymentProvider`. An optional live Test Mode path runs only when `MARGINMIND_LIVE_RAZORPAY=1` and both keys are set.

#### Checkout flow

```
request checkout
  → load session + granted exact approval + exact basket version
  → reject any caller amount/price/discounted total
  → M8 revalidate_approved_basket (required PASS)
  → amount_minor = catalogue live subtotal as integer paise
  → idempotent CheckoutAttempt (CHK-001)
  → provider create_order
  → persist provider_order_id + Payment (PAY-001, not verified)
  → return safe payload (key_id, order id, amount_minor, currency, CHK ref, merchant name)
  → await payment result (M10 verifies)
```

If any step before `create_order` fails, no Razorpay order is created. Revalidation failure does not occupy the idempotency key, so a later restock can succeed.

#### Amount authority

The Razorpay amount is integer **minor units** from server-side basket/catalogue truth. Never floats. INR: ₹2,447 → `244700` paise (`Decimal` quantized to 0.01, then × 100). Caller `amount` / `price` / `discounted_total` / `amount_minor` is rejected even if it matches.

#### Idempotency key

```
checkout:{session_ref}:{basket_ref}:v{version}:{approval_ref}
```

A repeated create-checkout for the same approved exact basket reuses the existing CheckoutAttempt and provider order when status is `ORDER_CREATED`, `CHECKOUT_PRESENTED`, `PAYMENT_REPORTED`, or `VERIFICATION_PENDING` and `amount_minor` still matches. `FAILED` attempts with the same key are retried in place after a new PASS. Duplicate requests must not create a second Razorpay order.

#### CheckoutAttempt status (closed)

`CREATED` · `REVALIDATION_REQUIRED` · `READY_FOR_PROVIDER` · `ORDER_CREATED` · `CHECKOUT_PRESENTED` · `PAYMENT_REPORTED` · `VERIFICATION_PENDING` · `VERIFIED` · `FAILED` · `CANCELLED`

This is distinct from basket `CheckoutState`. M9 normally stops at `ORDER_CREATED` / `CHECKOUT_PRESENTED`. **M9 never writes `VERIFIED`.** A browser/client Razorpay callback is stored as `PAYMENT_REPORTED` or `VERIFICATION_PENDING` only. Webhook and signature verification are M10.

HTTP (thin): `POST /api/v1/checkout`, `GET /api/v1/checkout/{ref_id}`, optional `POST /api/v1/checkout/{ref_id}/client-result`. Business logic is in `layers.checkout`.

### 6.9 Evidence / audit (`backend/app/layers/evidence/`)

Every consequential decision points at evidence IDs, not at “the model said so”.

Evidence examples: customer utterance, signal counts, basket snapshot, inventory row, policy check object, revalidation result (`REVAL-…`), checkout attempt (`CHK-…`). M8–M9 write these for later Agent Trace; the trace UI is not built yet.

Audit events are **append-only**:

`timestamp, actor, input_ref, decision, evidence_ids, policy_result, approval_ref, execution_result, verification_result`

Merchant Agent Trace is a projection of these events for one session.

### 6.10 Synthetic evaluation (`eval/`)

Runs **in-process** against Growth Decision Engine + Policy Engine + Catalogue (no browser, optional no HTTP).

- 50–100 JSON scenarios with labelled friction and expected action family.
- Metrics: diagnosis accuracy, action selection, policy violations, hallucinated SKUs, OOS proposals, STOP correctness, hard-budget violations.
- Prototype results labelled **Synthetic / offline evaluation**. Never claim live GMV uplift.

---

## 7. Primary demo data flow

This is the path that must work end-to-end for the Buildathon demo (spec §58).

```
Customer message
    → Intent extraction          (AI structured output, Pydantic-validated)
    → Catalogue retrieval        (hard filters on real SKUs)
    → Recommendations            (3 looks; AI may rank, code must have filtered)
    → Session signals            (frontend events: size guide, views, rejects)
    → Friction diagnosis         (rules first; AI may add confidence/copy)
    → Proposed bounded action    (Growth Decision Engine)
    → Policy validation          (deterministic; PASS / BLOCK / APPROVAL_REQUIRED)
    → Customer approval          (exact basket version; approval ≠ success)
    → Inventory/price/offer revalidation  (PASS / FAILED / STOPPED)
    → Razorpay checkout          (only if revalidation PASS)
    → Webhook verification       (signature + idempotency)
    → Audit trail
    → Merchant dashboard         (trace + guardrails + opportunity)
```

### 7.1 Sequence (happy path + hero failure)

1. Customer: *“Farewell next week. 5'2", hate tight waist, no clue, ₹2,500 max.”*
2. Stub/real LLM → `Intent` with `budget.type = HARD`, `fit_preferences = [relaxed_waist]`, `goal = complete_outfit`.
3. Catalogue returns in-budget, in-stock, size-available candidates. AI does not invent products.
4. Dress Me returns **3 looks**. Customer selects one. Basket Architect completes the look ≤ ₹2,500.
5. Signals: size guide opened repeatedly → friction `FIT_UNCERTAINTY` → `GUIDE_CONFIDENCE` (no discount).
6. Merchant attach-rate goal suggests a bag; basket + bag > hard budget → `NO_UPSELL`. Policy records the suppression.
7. Customer approves basket version N. Before pay, jeans OOS → **revalidation FAIL** → checkout blocked → alternative proposed → customer approves version N+1.
8. Razorpay test order created from frozen snapshot. Payment captured. Webhook verified. Session marked verified — not merely “approved”.
9. Merchant dashboard shows the same session as intent → evidence → friction → action → policy → approval → revalidation → payment → verification.

### 7.2 State machine (checkout)

```
DRAFT_BASKET
  → PENDING_APPROVAL
  → APPROVED_UNVERIFIED
  → REVALIDATING
  → READY_FOR_PAYMENT | REVALIDATION_FAILED
  → ORDER_CREATED
  → PAYMENT_PENDING
  → VERIFIED | PAYMENT_FAILED | ABANDONED
```

Only `VERIFIED` counts as a commercial success for metrics.

---

## 8. Core data model (MVP tables)

**M1 implemented** (UUID `id` + unique `ref_id` unless noted):

| Table | `ref_id` example | Role |
| --- | --- | --- |
| `merchants` | `MER-001` | Demo merchant |
| `customers` | `CUS-001` | Demo shopper |
| `customer_preferences` | `PREF-001` | HARD vs SOFT rows (`key`, `value`, `kind`) |
| `products` | `PRD-001` | Fashion metadata + `base_price` + `margin_band` + `margin_percent` |
| `product_variants` | `SKU-001-M` | Size/colour; `ref_id` **is** the SKU; optional `price_override` |
| `inventory` | (1:1 with variant) | `quantity`, `reserved_quantity`, `updated_at` |
| `merchant_policies` | `POL-001` | Structured `code` + typed value (not free-text-only) |
| `offers` | `OFR-001` | Authorised offers with eligibility, dates, stacking, min margin |
| `shopping_sessions` | `SES-001` | Table ready; not seeded in M1 |
| `session_events` | `EVT-001` | Behavioural evidence later |
| `intents` | `INT-001` | Structured session intent later |
| `baskets` | `BASK-001` + `version` | Unique `(ref_id, version)` → `BASK-001@v2` |
| `basket_items` | (line UUID) | Variant + qty + `unit_price_snapshot` |
| `approvals` | `APR-001` | Exact session + action + basket version. Grant ≠ execute |
| `evidence` | `EVD-001` | Evidence packs |
| `audit_events` | `AUD-001` | Append-only trace |
| `friction_diagnoses` | `FRIC-001` | Rule-first friction + evidence refs (M5) |
| `agent_actions` | `ACT-001` | Proposed bounded actions (M6). Not authorization. |
| `policy_decisions` | `PDEC-001` | Policy Engine verdict + typed checks (M7). Not execution. |
| `revalidation_results` | `REVAL-001` | Final live re-check of an exact approved basket (M8). |
| `checkout_attempts` | `CHK-001` | Idempotent checkout after revalidation PASS (M9). Amount in paise. |
| `payments` | `PAY-001` | Provider order/payment row. `verified_at` is M10. |

**Deferred:** `growth_opportunities`, `webhook_events`, `campaigns`, `experiments`, `experiment_assignments`, `demand_clusters`.

Product commercial fields: name, category, description, base price, colour, material, fit, silhouette, length, stretch, coverage, occasion tags, style tags, margin band, margin percent, active. Inventory is never stored as AI metadata.

Catalogue queries live in `app.layers.catalogue` (`get_product_by_ref_id`, `get_variant_by_sku`, `list_available_variants`, `get_available_quantity`, `is_available`). No AI ranking.

Seeded catalogue: **14 products / 24 variants**. Hero complete look: `SKU-004-M` + `SKU-007-M` + `SKU-011-OS` = ₹2,447.

### 8.1 Stable, human-readable reference IDs

Internal primary keys may be UUIDs. Entities that appear in Agent Trace, debugging, seed data, and synthetic evaluation also get a **stable, unique, human-readable `ref_id`**.

Traces, eval scenarios, and merchant UI should display `ref_id`, not UUIDs.

| Entity | Example `ref_id` |
| --- | --- |
| Merchant | `MER-001` |
| Customer | `CUS-001` |
| Customer preference | `PREF-001` |
| Product | `PRD-001` |
| Variant / SKU | `SKU-001-M` |
| Session | `SES-001` |
| Session event | `EVT-001` |
| Intent | `INT-001` |
| Basket | `BASK-001` |
| Basket version | `BASK-001@v2` (ref + integer version; never reuse a version) |
| Merchant policy | `POL-001` |
| Offer | `OFR-001` |
| Approval | `APR-001` |
| Evidence record | `EVD-001` |
| Friction diagnosis | `FRIC-001` |
| Audit event | `AUD-001` |
| Agent action | `ACT-001` |
| Policy decision | `PDEC-001` |
| Revalidation | `REVAL-001` |
| Checkout attempt | `CHK-001` |
| Payment | `PAY-001` |

Assign these in seed data and in application code when rows are created. Do not regenerate a `ref_id` after insert. Eval fixtures and the farewell demo session should use the same ID scheme so a judge can follow `SES-001` → `EVD-001` → `ACT-001` → `BASK-001@v2` on the trace page.

---

## 9. Bounded vocabularies (closed sets)

### Actions

`RECOMMEND` · `BUILD_BASKET` · `GUIDE_CONFIDENCE` · `SIMPLIFY_CHOICES` · `FIND_ALTERNATIVE` · `REBUILD_BASKET` · `APPLY_AUTHORIZED_OFFER` · `NO_UPSELL` · `REQUEST_CHECKOUT` · `STOP`

### Friction

`FIT_UNCERTAINTY` · `STYLE_UNCERTAINTY` · `COLOUR_UNCERTAINTY` · `BUDGET_MISMATCH` · `PRICE_HESITATION` · `SIZE_UNAVAILABLE` · `OUT_OF_STOCK` · `CHOICE_OVERLOAD` · `BASKET_INCOMPLETE` · `CATALOGUE_GAP` · `CHECKOUT_HESITATION` · `NONE` · `UNKNOWN`

### Policy verdict

`PASS` · `BLOCK` · `APPROVAL_REQUIRED`

### Revalidation status

`PASS` · `FAILED` · `STOPPED`

### Checkout attempt status

`CREATED` · `REVALIDATION_REQUIRED` · `READY_FOR_PROVIDER` · `ORDER_CREATED` · `CHECKOUT_PRESENTED` · `PAYMENT_REPORTED` · `VERIFICATION_PENDING` · `VERIFIED` · `FAILED` · `CANCELLED`

M9 stops at `ORDER_CREATED` / `CHECKOUT_PRESENTED`. Client-reported success is not `VERIFIED`.

### Payment status

`CREATED` · `REPORTED` · `VERIFICATION_PENDING` · `VERIFIED` · `FAILED`

These live in `backend/app/schemas/vocabulary.py` so frontend, engine, eval, and audit share one contract.

---

## 10. Repository structure

```
MarginMind/
  docs/
    ARCHITECTURE.md          ← this file
    BUILD_PLAN.md
    MarginMind — Product & Build Specification.md
  frontend/                  ← Next.js app (not scaffolded in M0)
    src/app/
    src/components/customer/
    src/components/merchant/
    src/lib/
  backend/                   ← FastAPI modular monolith
    app/
      api/v1/routes/         ← HTTP adapters
      core/                  ← config, orchestrator
      db/                    ← engine, sessions, migrations later
      models/                ← SQLAlchemy
      schemas/               ← Pydantic + shared vocabulary
      engines/
        growth_decision/     ← M6 proposes; does not authorize
        policy/              ← M7 validates; does not execute
      layers/
        catalogue/
        basket/
        approval/            ← exact-version grant/reject; no execution
        revalidation/        ← M8 live re-check; approval ≠ success
        checkout/            ← M9 attempt + state machine; no verified payment
        friction/
        evidence/
        payments/            ← PaymentProvider; Stub + Razorpay Test Mode
      providers/llm/         ← LLMProvider protocol
    tests/
  eval/
    harness/
    scenarios/
  data/seed/                 ← synthetic catalogue + demo merchant
  docker-compose.yml         ← local Postgres when M1 starts
```

No extra services. No frontend pages in M0. No dependency install in M0.

---

## 11. Real vs synthetic for the demo

| Must be real logic | Safe to seed / stub |
| --- | --- |
| Pipeline orchestration in the documented order | Fashion catalogue, stock, prices, tags |
| Policy Engine checks | Single demo merchant + demo customer |
| Versioned basket + approval binding | Session auth (simple token / hardcoded demo login) |
| Revalidation (including OOS stop) | LLM: Gemini free tier when wired; `StubLLMProvider` always available |
| Evidence IDs + append-only audit | `StubPaymentProvider` for automated tests; live Test Mode is opt-in |
| Bounded actions including `NO_UPSELL` / `STOP` | Hero OOS: scripted stock drop on the approved SKU |
| Hard-constraint catalogue filter | Conversational copy |
| Eval harness scoring engines | Demand Gap / campaigns / experiments / Couple Mode / vibe / colour |
| Razorpay Test Mode order after revalidation PASS | Checkout.js widget / customer frontend (later) |
| Webhook signature verify + idempotency **once M10 is wired** | Multi-merchant, production auth, email recovery |

**Hybrid (recommended):** friction diagnosis is **rule-first** from recorded signals so Scene 3–5 cannot fail because the model hedged. LLM adds intent structure and language.

---

## 12. Highest-risk areas (5-day deadline)

1. **Live LLM in the demo path** — Gemini free-tier rate limits, latency, schema drift, wrong friction, invented SKUs. Mitigation: `StubLLMProvider` fallback + Pydantic validation + rule-first friction; do not put Gemini on the critical demo path until the stub pipeline is green.
2. **Razorpay Test Mode + Checkout.js + webhook in the remaining calendar** — account, keys, CORS, tunnel/public URL for webhooks, signature bugs. Mitigation: payment interface first; stub until the commerce state machine is solid; wire Razorpay as its own milestone.
3. **Scope creep into fashion features** — vibe, colour, couple mode, CV try-on. Mitigation: locked MVP list is the only backlog until §63 “done” is true.
4. **Two polished UIs** — copilot + merchant centre will consume Day 3–5. Mitigation: shadcn, small page count, merchant trace is more important than a large dashboard.
5. **Catalogue metadata quality** — without fit/occasion/size/margin fields, Dress Me and eval are hollow. Mitigation: hand-authored seed of **10–15 products / ~24–36 variants**, not a scrape and not a 40–80 variant catalogue.
6. **Stale basket / approval / revalidation bugs** — silent substitution would violate the hero story. Mitigation: version numbers and “never mutate an approved snapshot”.
7. **Last-day Vercel/Render/CORS/env wiring** — Mitigation: keep API surface small; document env vars early; do a smoke deploy before polish.

---

## 13. What this milestone does not include

- No customer frontend / Checkout.js widget
- No webhook or signature verification (M10)
- No marking payment `VERIFIED` from the browser
- No Gemini / merchant dashboard / Agent Trace UI
- No live/prod Razorpay keys in the repository

Next: [BUILD_PLAN.md](./BUILD_PLAN.md) — M10 webhook/signature verification. Do not start M10 without approval.
