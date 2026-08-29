# MarginMind Build Plan

**Milestone:** 0 complete after the files in this commit-ready foundation exist.  
**Horizon:** ~5 days to a polished Buildathon MVP.  
**Spec:** [MarginMind — Product & Build Specification.md](./MarginMind%20—%20Product%20%26%20Build%20Specification.md)  
**Architecture:** [ARCHITECTURE.md](./ARCHITECTURE.md)

Each milestone below is **small, sequential, independently testable, and committable**. Do not start N+1 until N has a visible pass/fail check. Do not pull stretch features into an earlier milestone.

---

## Working rules

1. Locked MVP only (spec §62). Stretch is parked: Get the Vibe, colour guidance, Couple Mode, Demand Gap engine, campaigns, experiments, bulk actions, CV, social.
2. AI never writes commercial truth. If a task needs price, stock, eligibility, or payment state, it belongs in code.
3. MVP live LLM is the **Google Gemini API (free tier)** behind `LLMProvider`. `StubLLMProvider` remains the deterministic fallback so a model outage or free-tier limit does not kill the pitch. **Do not integrate Gemini in M0 or M1.**
4. Label eval results **synthetic / offline**. Never claim live revenue uplift.
5. One commit per milestone (or a short stack of commits that still map cleanly to one milestone).
6. Do not install dependencies before Milestone 1. Do not scaffold Next.js before Milestone 12.

---

## Calendar (indicative)

| Day | Milestones | Outcome |
| --- | --- | --- |
| 0 / start | **M0** | Architecture + empty modules |
| Day 1 | **M1–M4** | Data, catalogue, stub intent, baskets |
| Day 2 | **M5–M8** | Friction, GDE, policy, evidence |
| Day 3 | **M9–M11** | Razorpay test order + webhook verification + Agent Trace backend |
| Day 4 | **M12–M14** | Customer Copilot UI + synthetic eval |
| Day 5 | **M15** | Demo script, polish, deploy smoke |

Buffer lives in Day 5. If something slips, **cut UI chrome before cutting Policy, Revalidation, Audit, or Eval**.

---

## M0 — Architecture and repository foundation

**Status:** this document.

**In scope**

- Read spec; lock architecture; write these docs; placeholder packages only.

**Out of scope**

- Dependencies, features, pages, payments, LLM vendors.

**Test**

- Repo layout matches `ARCHITECTURE.md` §10. No runtime required.

**Exit**

- `docs/ARCHITECTURE.md`, `docs/BUILD_PLAN.md`, placeholder modules present. Wait for approval before M1.

---

## M1 — Data model, Postgres, seed catalogue

**Status:** complete.

**In scope (done)**

- Docker Compose Postgres (canonical local DB). Workspace-local Postgres is an allowed verify fallback if Docker is missing.
- SQLAlchemy 2 models for the M1 table set in `ARCHITECTURE.md` §8.
- Alembic revision `m1_initial`.
- Seed: merchant `MER-001`, customer `CUS-001`, policies `POL-001`–`POL-008`, offers `OFR-001`–`OFR-003`, **14 products / 24 variants**.
- Catalogue/inventory query functions (pulled forward from original M2 because M1 required a usable truth layer).
- Dual identifiers: UUID PK + stable `ref_id`.

**Out of scope (held)**

- Growth Decision Engine, Policy Engine runtime, frontend, Razorpay, Gemini.

**Verified**

- `alembic upgrade head`
- Seed twice with unchanged row counts
- pytest: seeded SKU exists; unknown SKU fails closed; size/OOS/price-ceiling filters; idempotent seed

---

## M2 — Catalogue retrieval with hard filters

**Status:** complete.

HARD `CatalogueConstraints` exclude candidates before any ranking. SOFT `SoftCatalogueSignals` are accepted and never used to exclude. Restricted SKUs/products, materials, coverage, inactive rows, OOS, budget, required size, and unknown merchant/SKU all fail closed. One-size (`OS`) accessories remain eligible when `required_size` is set unless `allow_one_size=False`.

**Out of scope**

- LLM ranking, baskets, HTTP.

**Test**

- Unit tests: in-budget in-stock hit; OOS excluded; invented SKU lookup fails; hard constraint excludes matching tag.

**Commit**

- Catalogue module + tests.

---

## M3 — Session, intent schema, stub LLM provider

**Status:** complete.

Customer message → session event + evidence → `StubLLMProvider` → validated `IntentExtractionResult` → persisted `INT-…` → HARD/SOFT catalogue mapping → eligible real SKUs.

Unknown utterances stay unknown. Provider errors do not persist a fake intent. Gemini is not integrated. Basket-total budget is not enforced here.

**In scope**

- Pydantic `Intent` (occasion, budget amount/type, height, fit_preferences, style_preferences, goal, hard vs soft).
- `LLMProvider` protocol + `StubLLMProvider` mapping known demo utterances (and eval ids) to intents.
- Session create + store latest intent. New sessions get `ref_id` values such as `SES-001`.
- Orchestrator step 1 only: message → intent → persist.

**Out of scope**

- Gemini API client / any live LLM SDK. Conversational UI.
- (`GeminiLLMProvider` is the intended MVP live implementation; wire it in a later milestone after this stub path is green.)

**Test**

- Farewell demo sentence → `budget.type=HARD`, `amount=2500`, `relaxed_waist` present.
- Garbage/unknown stub input → safe empty/low-confidence intent, not a fabricated SKU.

**Commit**

- Schemas, stub provider, session persistence.

---

## M4 — Basket Architect (deterministic)

**Status:** complete.

Versioned baskets, catalogue-authoritative prices, **total** HARD budget validation, deterministic complete-look builder (up to 3), NO_UPSELL add-on helper, replacement proposal (no auto-swap). No Gemini, GDE, or discounts.

**In scope**

- Build 3 looks from filtered catalogue (Dress Me without chat UI: function + later API).
- Given a selected core item, attach compatible items so total ≤ hard budget.
- Versioned `baskets` / `basket_items` with `ref_id`s such as `BASK-001` / `BASK-001@v1`.
- Never add an item that breaks hard budget (`NO_UPSELL` path can be a return code).

**Out of scope**

- Friction engine, offers, UI.

**Test**

- Core trousers ₹1,399 + budget ₹2,500 → completed basket ≤ 2500 with real SKUs.
- Cannot attach an accessory that crosses the cap.

**Commit**

- Basket layer + tests.

---

## M5 — Session signals and rule-based friction diagnosis

**Status:** complete.

Typed session signals (`EVT-…` + `EVD-…`), deterministic friction resolver, ranked diagnoses with WHAT/WHY, persistence (`FRIC-…`). No Gemini, GDE, FIX, or bounded actions.

**In scope (done)**

- Record signals: view counts, size-guide opens, recommendation rejects, checkout-started, comparison events.
- Deterministic mapper to `FrictionType` + documented confidence steps.
- Persist `friction_diagnoses` with evidence ids.

**Out of scope (held)**

- Action selection, LLM friction override.

**Test**

- Fixture sessions for fit, budget, OOS, choice-overload, none — expected labels.

**Commit**

- Signals + diagnosis.

---

## M6 — Growth Decision Engine

**Status:** complete.

Deterministic friction → bounded `ProposedAction` (`ACT-…`), WHAT/WHY/FIX, rescue hierarchy, `NO_UPSELL` / `STOP`. Proposal is not permission. No Gemini, no Policy Engine runtime, no basket mutation.

**Out of scope (held)**

- Policy module internals, execution, payments.

**Test**

- Fit signals → `GUIDE_CONFIDENCE` not `APPLY_AUTHORIZED_OFFER`.
- Hard budget + attach-rate temptation → `NO_UPSELL`.
- No valid inventory → `STOP` / `FIND_ALTERNATIVE` as specified, never a fake SKU.
- Catalogue gap → no hallucination.

**Commit**

- GDE + tests.

---

## M7 — Policy Engine

**Status:** complete.

Deterministic `validate_action` over database-backed commercial truth. Closed verdicts `PASS` / `BLOCK` / `APPROVAL_REQUIRED`. Proposal is not permission. Approvals bind to exact basket versions (`BASK-001@v1` never covers `@v2`). Granting approval does not execute. No Gemini, no Razorpay, no frontend, no checkout execution.

**Out of scope (held)**

- Execution after approval, revalidation-before-pay, Policy Studio UI.

**Test**

- Hero fit → `GUIDE_CONFIDENCE` → PASS, no approval, no basket mutation.
- Hero `NO_UPSELL` → PASS; forgone revenue recorded; budget protected.
- Valid rebuild → `APPROVAL_REQUIRED`; over-budget GDE lie → `HARD_BUDGET_VIOLATION` BLOCK.
- Unknown/inactive/expired offer, margin floor, discount max, stacking, basket minimum → BLOCK.
- Stale v1 approval does not authorize v2.

**Commit**

- Policy engine + approval service + tests.

---

## M8 — Final revalidation + OOS rescue + re-approval

**Status:** complete.

**Approval ≠ success.** Exact approved basket is frozen, then re-checked from live catalogue/inventory/offer/margin/policy truth (`REVAL-…`). OOS/price/offer failures do not silently mutate the approved snapshot. Rescue proposes a real replacement; accepting forks `BASK-001@v2` which requires a **new** approval. Repeated unchanged revalidation is idempotent. No Razorpay, no Gemini, no frontend, no Agent Trace UI.

**Out of scope (held)**

- Payment execution, webhooks, Agent Trace UI, merchant dashboard.

**Test**

- Unchanged approved basket → PASS.
- Hero OOS on `SKU-004-M` → FAILED, basket unchanged, then real replacement, new version, old APR does not cover v2.
- Price change → PRICE_CHANGED. Offer expiry → STOP. Stale v1 APR + v2 basket → STOP.
- No valid substitute → STOP, no invented SKU.

**Commit**

- Revalidation + OOS rescue + tests.

---

## M9 — Razorpay Test Mode order + checkout state machine

**Status:** complete.

**Goal:** After customer approval → exact basket version → M8 revalidation PASS, create an idempotent checkout attempt and a Razorpay **Test Mode** order. Client-side checkout success is not verified payment.

```
approved exact basket → live revalidation PASS → CheckoutAttempt (CHK-…)
  → provider order → safe checkout payload → await payment result (M10)
```

**In scope**

- `PaymentProvider` with `StubPaymentProvider` (tests) and `RazorpayPaymentProvider` (Test Mode via httpx; no official SDK in the app layer).
- `CheckoutAttempt` (`CHK-001`) and `Payment` (`PAY-001`). Amounts are integer paise. ₹2,447 → 244700.
- Closed checkout statuses. M9 stops at `ORDER_CREATED` / `CHECKOUT_PRESENTED`. Never `VERIFIED`.
- Checkout service: reject caller price, require granted exact approval, run M8, require PASS, compute server amount, idempotent attempt, create order, persist `provider_order_id`, return `key_id` + order id + amount + currency + CHK ref.
- Idempotency key: `checkout:{session_ref}:{basket_ref}:v{version}:{approval_ref}`. Reuse a valid attempt; retry `FAILED` in place; do not occupy the key on revalidation failure.
- Optional client-result recorder: `PAYMENT_REPORTED` / `VERIFICATION_PENDING` only.
- Thin FastAPI: `POST /api/v1/checkout`, `GET /api/v1/checkout/{ref_id}`, health. Secrets from env only.

**Out of scope**

- Customer frontend / Checkout.js. Gemini. Merchant dashboard. Agent Trace UI.
- Webhook or signature verification (M10).
- Trusting client amount or marking `VERIFIED` from the browser.

**Test**

- Hero approved look → PASS → stub order, amount 244700, CHK/APR/REVAL recorded, payment not verified.
- OOS, price change, invalid offer, stale v1→v2, hard-budget → no provider order.
- Duplicate create-checkout → one order. Provider failure → `FAILED`, retry in place.
- Client `VERIFIED` callback does not set `verified_at`. Stub works offline. Live Razorpay is opt-in (`MARGINMIND_LIVE_RAZORPAY=1`).

**Commit**

- Checkout + payments layer + FastAPI checkout routes + tests.

---

## M10 — Webhook + signature verification

**Status:** complete.

**Goal:** Independent verification of money movement. A browser “payment succeeded” is not commercial truth.

```
client may report → VERIFICATION_PENDING
  → POST /api/v1/webhooks/razorpay
  → HMAC-SHA256(raw body, RAZORPAY_WEBHOOK_SECRET)
  → persist WHK-… (dedupe on x-razorpay-event-id)
  → correlate order/amount/currency
  → payment.captured | order.paid → VERIFIED (once)
```

**In scope**

- Raw-body HMAC. `X-Razorpay-Signature`. Constant-time compare. Do not parse JSON first.
- `RAZORPAY_WEBHOOK_SECRET` distinct from `RAZORPAY_KEY_SECRET`.
- `webhook_events` (`WHK-001`). Statuses: RECEIVED, VERIFIED_SIGNATURE, PROCESSED, DUPLICATE, IGNORED, FAILED.
- Supported: `payment.authorized` (not verified), `payment.captured` / `order.paid` (may verify), `payment.failed` (never downgrades VERIFIED).
- Amount/currency/order correlation. SHA-256 of raw body. Unique provider payment id.
- Optional stub `fetch_payment` recovery path. Automated tests stay offline.

**Out of scope**

- Live/prod keys. Refunds. Subscriptions. Customer frontend. Agent Trace UI.

**Test**

- Hero checkout + client success → not VERIFIED; signed `payment.captured` → VERIFIED once.
- Invalid/missing signature, amount 200000 vs 244700, unknown order, duplicate event id, captured then authorized, captured then order.paid, failed then captured, unsupported event.

**Commit**

- Webhook verification + `webhook_events` + tests.

---

## Later — Customer Copilot UI

**Moved to proposed M12.** Previously listed as M9, then as proposed M11.

**Goal:** Judges can play Scene 1–6 without calling curl.

Approval, revalidation, checkout-order, webhook, and Agent Trace APIs already exist. This milestone is the customer surface that calls them, including Checkout.js against the M9 payload (widget only; verification remains M10). Progress states can consume `GET /api/v1/sessions/{session_ref}/progress`.

**Out of scope at that time:** Merchant UI. Colour/vibe/couple. Agent Trace page (merchant `GET .../trace` is already the data contract).

---

## Later — Merchant Growth Control Centre UI

**Goal:** Reveal that the copilot was a decision engine.

**In scope**

- `/merchant`: top opportunities (even if few, derived from open frictions / failed revalidations / NO_UPSELL).
- Opportunity detail: What / Why / Fix.
- Policy studio: view/edit margin, max discount, stacking, approval flags (simple form).
- Guardrail counters + audit list.
- Agent Trace timeline UI over `GET /api/v1/sessions/{session_ref}/trace`.

**Out of scope**

- Experiments, campaign agent, demand-gap product. Trace *data* is M11; this is the merchant page.

---

## M11 — Agent Trace + audit reconstruction backend

**Status:** complete.

**Goal:** Turn persisted session, evidence, friction, action, policy, approval, revalidation, checkout, payment, webhook, and audit records into one coherent Agent Trace. Prepare the data contract for later customer progress UI and merchant Agent Trace UI. Does not build frontend.

```
GET /api/v1/sessions/{session_ref}/trace      merchant / full
GET /api/v1/sessions/{session_ref}/progress   customer-safe projection
```

`build_agent_trace(db, session_ref_id)` reconstructs from the database only. A session from an earlier process restart is still reconstructable. No Gemini. No payment-behavior change. No new commercial tables.

**In scope**

- Typed `AgentTrace` / `CustomerProgress` schemas and closed `TraceEventType` / `TraceOutcome`.
- Chronological timeline from persisted facts only (no fabricated `CATALOGUE_RETRIEVED`).
- Structured WHAT / WHY / FIX; typed policy checks; exact-version approvals; revalidation; distinct checkout / client / webhook / payment stages.
- Safe webhook metadata (no secret, no raw body).
- Guardrail summary derived from executed commercial truth, not hard-coded zeros.
- Deterministic final-outcome precedence (`PAYMENT_VERIFIED` wins over earlier client noise).
- Stable secondary ordering when timestamps tie.

**Out of scope**

- Customer Copilot UI. Merchant Trace UI. Gemini. New autonomy. Campaigns. Table redesign.

**Test**

- Hero fit: intent → friction → action → policy, evidence refs preserved.
- Hero OOS: v1 approval does not cover v2; replacement + new approval.
- Hero payment: `CLIENT_PAYMENT_REPORTED` ≠ `PAYMENT_VERIFIED`.
- NO_UPSELL: action + `HARD_BUDGET_VIOLATION` reason, `hard_budget_violation_count = 0`.
- Blocked GDE proposal: policy BLOCK, no execution.
- Reconstruction after a new SQLAlchemy session.
- Customer projection hides `margin_percent` and forgone revenue.

**Commit**

- Trace reconstruction service + HTTP GET + tests.

Do not start M12 without approval.

---

## M12 — Thin HTTP adapters + Customer Copilot UI (proposed)

**Goal:** The copilot can call remaining M3–M8 flows without embedding SQLAlchemy, then judges can play Scene 1–6 in a UI.

Checkout HTTP already exists from M9. Trace HTTP exists from M11. This milestone adds session, message, signal, basket, approve, revalidate, and replacement routes as needed, plus the customer surface including Checkout.js against the M9 payload. Webhooks remain the source of `VERIFIED`.

---

## M13 — (absorbed)

Razorpay Test Mode **order creation** is M9. **Webhook/signature verification** is M10. Checkout.js lives with the later customer UI. Do not re-implement order creation here.

---

## M14 — Synthetic evaluation harness

**Goal:** “Not three hand-picked shoppers.”

**In scope**

- 50–100 labelled JSON scenarios (mix from spec §57, scaled), citing catalogue/session `ref_id`s (`SKU-001-M`, `SES-…`, not UUIDs).
- In-process runner over GDE + policy + catalogue (+ stub LLM; Gemini optional, not required for the harness).
- Report: friction accuracy, action family match, policy violations, hallucinated SKU count, OOS proposed count, STOP correctness, hard-budget violations.
- Targets: **0** hard-budget violations, hallucinated SKUs, unauthorized offers, unapproved money actions, duplicate payment processing (payment cases can be stubbed).

**Out of scope**

- Claiming the numbers are production A/B results.

**Test**

- `python -m eval.harness` exits non-zero if safety counters ≠ 0.
- README shows how to paste the summary into the demo.

**Commit**

- Scenarios + harness + sample report artifact (generated, not hand-faked).

---

## M15 — Demo hardening and polish

**Goal:** One rehearsal of spec §58 Scenes 1–7 plus eval slide.

**In scope**

- Seed a deterministic demo script (utterances, signal sequence, OOS trigger).
- Copy pass on customer + merchant.
- Deploy smoke: Vercel frontend, Render backend, env vars, CORS, webhook URL.
- Kill-switch: if Razorpay webhook cannot be reached in the venue, documented fallback (client success + **server fetch payment** still required; never client-only “paid”).
- LLM kill-switch: `LLM_PROVIDER=stub` if Gemini free-tier limits or latency threaten the live demo.

**Out of scope**

- New features.

**Test**

- Timed dry run of the 7 scenes. Eval command run once on stage machine or recorded output.

**Commit**

- Demo notes in `docs/DEMO.md` only if needed at that time; no new product surface.

---

## Explicitly parked (do not schedule unless MVP is done)

- Get the Vibe, colour guidance, Learn My Style as a full onboarding product, Couple Mode
- Demand Gap Engine, root-cause alerting at scale, priority engine sophistication, campaigns, experiments
- Computer vision, virtual try-on, dynamic pricing, multi-tenant SaaS hardening

---

## Proposed next milestone after approval

**M12 — Customer Copilot UI.**

Payment truth and Agent Trace reconstruction are complete. Build the customer surface that calls M7–M11, including Checkout.js against the M9 payload and progress from `GET .../progress`. Webhooks remain the source of `VERIFIED`. Do not start M12 without approval. Do not treat the widget callback as paid.

