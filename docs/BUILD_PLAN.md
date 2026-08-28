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
6. Do not install dependencies before Milestone 1. Do not scaffold Next.js before Milestone 11.

---

## Calendar (indicative)

| Day | Milestones | Outcome |
| --- | --- | --- |
| 0 / start | **M0** | Architecture + empty modules |
| Day 1 | **M1–M4** | Data, catalogue, stub intent, baskets |
| Day 2 | **M5–M8** | Friction, GDE, policy, evidence |
| Day 3 | **M9–M12** | Approval, revalidation, both UIs |
| Day 4 | **M13–M14** | Razorpay + synthetic eval |
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

**Goal:** The model cannot be the catalogue.

**Already landed in M1:** SKU lookup, in-stock listing, category/size/price-ceiling/tag filters, unknown SKU fails closed.

**Remaining in M2**

- Restricted-SKU exclusion from merchant policy/data
- Explicit HARD customer constraints as catalogue filters (e.g. coverage / no sleeveless) once those constraints exist on the session
- Keep retrieval deterministic; still no AI ranking

**Out of scope**

- LLM ranking, baskets, HTTP.

**Test**

- Unit tests: in-budget in-stock hit; OOS excluded; invented SKU lookup fails; hard constraint excludes matching tag.

**Commit**

- Catalogue module + tests.

---

## M3 — Session, intent schema, stub LLM provider

**Goal:** Ambiguous language becomes a structured object via `LLMProvider`, without coupling the pipeline to Gemini yet.

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

**Goal:** Complete a look inside a hard budget using real SKUs.

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

**Goal:** Hesitation is evidenced, not guessed from vibes alone.

**In scope**

- Record signals: view counts, size-guide opens, recommendation rejects, checkout-started, comparison events.
- Deterministic mapper to `FrictionType` + confidence band (e.g. size-guide ≥3 → `FIT_UNCERTAINTY`; basket > hard budget → `BUDGET_MISMATCH`; selected size stock 0 → `SIZE_UNAVAILABLE`).
- Persist `friction_diagnoses` with evidence ids.

**Out of scope**

- Action selection, LLM friction override.

**Test**

- Fixture sessions for fit, budget, OOS, choice-overload, none — expected labels.

**Commit**

- Signals + diagnosis.

---

## M6 — Growth Decision Engine

**Goal:** Smallest valid **proposed** action; including `NO_UPSELL` and `STOP`.

**In scope**

- Map friction → candidate actions (spec §17 table).
- Price/budget rescue hierarchy (spec §22).
- Closed `BoundedAction` vocabulary only.
- Emit `ProposedAction` with what/why/fix fields pointing at evidence ids.
- Confidence rule: low/medium → guide/ask, not discount.

**Out of scope**

- Policy module internals (call a temporary “always PASS” only if needed for wiring; prefer to land M7 immediately after).
- Execution, payments.

**Test**

- Fit signals → `GUIDE_CONFIDENCE` not `APPLY_AUTHORIZED_OFFER`.
- Hard budget + attach-rate temptation → `NO_UPSELL`.
- No valid inventory → `STOP` / `FIND_ALTERNATIVE` as specified, never a fake SKU.
- Catalogue gap → no hallucination.

**Commit**

- GDE + tests.

---

## M7 — Policy Engine

**Goal:** Code decides whether the proposal is allowed.

**In scope**

- `validate_action(action, context)` with MVP checks in `ARCHITECTURE.md` §6.5.
- Autonomy levels per action type.
- Structured per-check results.
- Merchant policy rows drive thresholds (margin, max discount, stacking, approval required).

**Out of scope**

- UI for Policy Studio (read API can wait until M12; engine must work now).

**Test**

- Offer proposed but margin fail → BLOCK.
- Hard budget fail → BLOCK / force `NO_UPSELL`.
- `APPLY_AUTHORIZED_OFFER` with unknown offer id → BLOCK.
- `RECOMMEND` in-stock in-budget → PASS, AUTO.
- `REQUEST_CHECKOUT` without approval → REQUIRE_APPROVAL or BLOCK.

**Commit**

- Policy engine + tests.

---

## M8 — Evidence store and append-only audit

**Goal:** The merchant can reconstruct a decision.

**In scope**

- `evidence_records` + `audit_events` writers used by orchestrator.
- Stable `ref_id`s on evidence (`EVD-001`), agent actions (`ACT-001`), and audit events (`AUD-001`).
- Trace query: ordered events for a session (intent, signals, friction, proposal, policy, approval, revalidation, payment), keyed by human-readable ids.
- Guardrail counters derived from audit: hard-budget violations, invented SKU attempts, unauthorized offers, unapproved money actions (all should stay 0).

**Out of scope**

- Merchant UI.

**Test**

- Running M4–M7 path writes a trace with evidence ids (`EVD-…`) that resolve.
- Audit table is insert-only in code (no update API).

**Commit**

- Evidence/audit layer + trace API (can be internal function until routers exist).

---

## M9 — Customer approval and basket freeze

**Goal:** Agency is explicit. Approval is not success.

**In scope**

- Approve endpoint binds customer + basket `ref_id` + version (`BASK-001@v2`) + line snapshot.
- Mismatch of version → reject.
- Transition to `APPROVED_UNVERIFIED`.
- Freeze snapshot for checkout.

**Out of scope**

- Razorpay. Revalidation (next).

**Test**

- Approve current version → stored approval.
- Mutate basket after approval without new approve → checkout must not see a valid freeze of the new lines (or version bump invalidates old approval).

**Commit**

- Approval layer + tests.

---

## M10 — Revalidation and hero OOS path

**Goal:** Stale commerce state cannot be charged.

**In scope**

- Revalidate: SKU, variant, size, qty, price unchanged, offer, margin, campaign/offer active, hard budget, approval matches exact snapshot.
- Any fail → `REVALIDATION_FAILED`, block checkout, do not silent-swap.
- `FIND_ALTERNATIVE` using catalogue + GDE; new basket version; require new approval.
- Seed/script helper to zero stock on a SKU (demo Scene 5).

**Out of scope**

- Payment provider.

**Test**

- Happy revalidation PASS.
- Price change or OOS → FAIL + no order created.
- Alternative is a real in-stock SKU within budget; old approval cannot be reused.

**Commit**

- Revalidation + alternative flow tests.

---

## M11 — Customer Copilot UI

**Goal:** Judges can play Scene 1–6 without calling curl.

**In scope**

- Scaffold Next.js + TS + Tailwind + shadcn.
- Pages: chat/Dress Me, 3 looks, basket, approval, “revalidation failed” replacement prompt, checkout placeholder (stub pay button until M13).
- Signal capture wired (size-guide clicks, rejects).
- API client to FastAPI.

**Out of scope**

- Merchant UI. Real Razorpay.js. Colour/vibe/couple.

**Test**

- Browser: farewell prompt → 3 looks → select → basket ≤ 2500 → fit-guide prompt from signals → approve.
- Manual OOS script → replacement prompt appears; cannot pay until re-approved.

**Commit**

- Frontend customer surface + backend routers needed for it.

---

## M12 — Merchant Growth Control Centre UI

**Goal:** Reveal that the copilot was a decision engine.

**In scope**

- `/merchant`: top opportunities (even if few, derived from open frictions / failed revalidations / NO_UPSELL).
- Opportunity detail: What / Why / Fix.
- Session agent trace (clickable timeline).
- Policy studio: view/edit margin, max discount, stacking, approval flags (simple form).
- Guardrail counters + audit list.

**Out of scope**

- Experiments, campaign agent, demand-gap product, background-job theatre beyond counts from real events.

**Test**

- Same session from M11 appears in trace with policy and approval steps.
- `NO_UPSELL` visible. Guardrails show 0 violations after a clean run.

**Commit**

- Merchant surface.

---

## M13 — Razorpay Test Mode, webhooks, idempotency

**Goal:** Independent verification of money movement.

**In scope**

- `RazorpayPaymentProvider` behind the same interface as the stub.
- Create test order only after revalidation PASS.
- Checkout.js on customer UI.
- Webhook: signature verification, idempotent processing, payment row, session → `VERIFIED`.
- Duplicate delivery does not double-count.

**Out of scope**

- Live/prod keys. Refunds. Subscriptions.

**Test**

- Test payment success → verified order + audit event.
- Replay webhook → one payment row.
- Invalid signature → ignored, not verified.
- LLM/backend logs contain no key material.

**Commit**

- Payments layer + webhook route + frontend widget.

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

**M2 — remaining hard-constraint catalogue filters** (restricted SKUs, HARD coverage constraints), unless you prefer to skip to **M3 — session + stub LLM**.

M1 is the commercial-truth foundation. Do not start M2 without approval.
