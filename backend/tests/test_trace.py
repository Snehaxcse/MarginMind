"""M11: read-only Agent Trace reconstruction from persisted records."""

from __future__ import annotations

import json
from decimal import Decimal

from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.api.deps import get_db
from app.core.pipeline import process_customer_message
from app.core.ref_ids import RefPrefix, next_numeric_ref_id
from app.db.session import get_session_factory
from app.engines.growth_decision import DEMO_ATTACH_SKU, propose_growth_action
from app.engines.policy import validate_action
from app.layers.approval import (
    approve,
    create_approval_request,
    reject,
    version_approval_covers,
)
from app.layers.basket import create_basket, get_basket, set_items, version_label
from app.layers.catalogue import set_on_hand_quantity
from app.layers.checkout import (
    create_checkout_attempt,
    process_webhook,
    report_client_payment_result,
)
from app.layers.evidence import record_evidence
from app.layers.friction import diagnose_friction, record_session_signal
from app.layers.intent import persist_intent
from app.layers.payments import StubPaymentProvider, encode_razorpay_event
from app.layers.revalidation import (
    accept_oos_replacement,
    propose_oos_replacement,
    revalidate_approved_basket,
)
from app.layers.session import create_session
from app.layers.trace import build_agent_trace, project_customer_progress
from app.main import app
from app.models import AgentAction
from app.providers.llm.stub import HERO_UTTERANCE, StubLLMProvider
from app.schemas.action import ProposedAction
from app.schemas.friction import SessionSignalInput
from app.schemas.intent import BudgetIntent, IntentExtractionResult, ShopperIntent
from app.schemas.vocabulary import (
    ActionStatus,
    BoundedAction,
    BudgetType,
    CheckStatus,
    EvidenceKind,
    FrictionType,
    PolicyCheckName,
    PolicyVerdict,
    SessionEventType,
    TraceEventType,
    TraceOutcome,
)

HERO = ["SKU-004-M", "SKU-007-M", "SKU-011-OS"]
HERO_PAISE = 244700


def _session(db: Session):
    return create_session(db, merchant_ref_id="MER-001", customer_ref_id="CUS-001")


def _hard(amount: str = "2500") -> ShopperIntent:
    return ShopperIntent(
        budget=BudgetIntent(amount=Decimal(amount), type=BudgetType.HARD),
        goal="complete_outfit",
        usual_size="M",
        fit_preferences=["relaxed_waist"],
        occasion="farewell",
    )


def _persist_intent(db: Session, shopping, intent: ShopperIntent):
    evidence = record_evidence(
        db,
        session=shopping,
        kind=EvidenceKind.CUSTOMER_MESSAGE.value,
        summary="intent seed",
        payload={"source": "test"},
    )
    persist_intent(
        db,
        session=shopping,
        extraction=IntentExtractionResult(
            intent=intent,
            confidence=0.95,
            evidence_ref_ids=[evidence.ref_id],
        ),
    )


def _signal(shopping, db: Session, event_type: SessionEventType, **payload) -> None:
    record_session_signal(
        db, session=shopping, signal=SessionSignalInput(event_type=event_type, **payload)
    )


def _fit_signals(shopping, db: Session) -> None:
    for _ in range(3):
        _signal(shopping, db, SessionEventType.SIZE_GUIDE_OPENED, sku="SKU-004-M")
    _signal(
        shopping,
        db,
        SessionEventType.PRODUCT_COMPARED,
        sku="SKU-004-M",
        sku_b="SKU-001-M",
        dimension="fit",
    )
    _signal(shopping, db, SessionEventType.FIT_QUESTION_ASKED, text="Will the waist bunch?")


def _types(trace) -> list[TraceEventType]:
    return [event.type for event in trace.timeline_events]


def _index(trace, event_type: TraceEventType) -> int:
    types = _types(trace)
    assert event_type in types, f"{event_type} missing from {_compact(types)}"
    return types.index(event_type)


def _compact(types: list[TraceEventType]) -> list[str]:
    return [item.value for item in types]


def _named_check(trace, name: PolicyCheckName):
    assert trace.policy_decisions
    return next(item for item in trace.policy_decisions[0].checks if item.name is name)


def _draft(
    shopping,
    action: BoundedAction,
    *,
    candidate_skus: list[str],
    evidence_ref_id: str,
    friction_type: FrictionType = FrictionType.BUDGET_MISMATCH,
) -> ProposedAction:
    return ProposedAction(
        session_ref_id=shopping.ref_id,
        friction_type=friction_type,
        action=action,
        reason="invalid GDE proposal",
        evidence_ref_ids=[evidence_ref_id],
        candidate_skus=candidate_skus,
        confidence=Decimal("0.92"),
        what="over-budget rebuild",
        why="test",
        fix="should be blocked",
    )


def test_trace_event_and_outcome_vocabularies_are_closed() -> None:
    assert TraceEventType.PAYMENT_VERIFIED in TraceEventType
    assert TraceOutcome.PAYMENT_VERIFIED in TraceOutcome
    assert set(TraceOutcome) == {
        TraceOutcome.IN_PROGRESS,
        TraceOutcome.STOPPED,
        TraceOutcome.CHECKOUT_READY,
        TraceOutcome.PAYMENT_PENDING_VERIFICATION,
        TraceOutcome.PAYMENT_VERIFIED,
        TraceOutcome.PAYMENT_FAILED,
        TraceOutcome.PURCHASE_PLAN_REJECTED,
    }


def test_hero_fit_trace_orders_intent_friction_action_policy(db: Session) -> None:
    shopping = _session(db)
    process_customer_message(
        db,
        session_ref_id=shopping.ref_id,
        message=HERO_UTTERANCE,
        provider=StubLLMProvider(),
    )
    set_items(db, create_basket(db, shopping), HERO)
    _fit_signals(shopping, db)
    intent = _hard()
    diagnosis = diagnose_friction(db, shopping, intent=intent)
    assert diagnosis.primary.friction_type is FrictionType.FIT_UNCERTAINTY
    proposal = propose_growth_action(db, shopping, intent=intent, diagnosis=diagnosis)
    assert proposal.action is BoundedAction.GUIDE_CONFIDENCE
    result = validate_action(db, shopping, proposal, intent=intent)
    assert result.decision is PolicyVerdict.PASS

    trace = build_agent_trace(db, shopping.ref_id)
    assert _index(trace, TraceEventType.INTENT_EXTRACTED) < _index(trace, TraceEventType.FRICTION_DIAGNOSED)
    assert _index(trace, TraceEventType.FRICTION_DIAGNOSED) < _index(trace, TraceEventType.ACTION_PROPOSED)
    assert _index(trace, TraceEventType.ACTION_PROPOSED) < _index(trace, TraceEventType.POLICY_VALIDATED)

    friction = next(item for item in trace.timeline_events if item.type is TraceEventType.FRICTION_DIAGNOSED)
    action = next(item for item in trace.timeline_events if item.type is TraceEventType.ACTION_PROPOSED)
    assert friction.what_why_fix is not None
    assert friction.what_why_fix.what == FrictionType.FIT_UNCERTAINTY.value
    assert "SIZE_GUIDE_REPEATED" in friction.what_why_fix.why
    assert "FIT_COMPARISON" in friction.what_why_fix.why
    assert "FIT_QUESTION" in friction.what_why_fix.why
    assert action.what_why_fix is not None
    assert action.what_why_fix.what == FrictionType.FIT_UNCERTAINTY.value
    assert action.what_why_fix.fix == BoundedAction.GUIDE_CONFIDENCE.value
    assert friction.evidence_ref_ids
    assert action.evidence_ref_ids
    assert _named_check(trace, PolicyCheckName.HARD_BUDGET).status in {CheckStatus.PASS, CheckStatus.NA}
    assert _named_check(trace, PolicyCheckName.INVENTORY).status in {CheckStatus.PASS, CheckStatus.NA}
    assert trace.policy_decisions[0].decision is PolicyVerdict.PASS
    assert trace.guardrails.hard_budget_violation_count == 0
    assert trace.guardrails.invented_sku_count == 0
    assert trace.outcome is TraceOutcome.IN_PROGRESS
    first = _types(trace)
    second = _types(build_agent_trace(db, shopping.ref_id))
    assert first == second


def test_hero_oos_trace_keeps_exact_version_approvals(db: Session) -> None:
    shopping = _session(db)
    intent = _hard()
    _persist_intent(db, shopping, intent)
    basket = set_items(db, create_basket(db, shopping), HERO)
    request = create_approval_request(db, shopping, basket, action_ref_id="ACT-CHK")
    approve(db, request.ref_id)
    set_on_hand_quantity(db, "SKU-004-M", 0)
    result = revalidate_approved_basket(db, shopping, request.ref_id, intent=intent)
    proposal = propose_oos_replacement(db, shopping, result, intent)
    assert proposal is not None
    decision = accept_oos_replacement(db, shopping, proposal, intent)
    v2 = get_basket(db, basket.ref_id, version=decision.new_basket_version)
    pending = create_approval_request(db, shopping, v2, action_ref_id="ACT-CHK-2")

    trace = build_agent_trace(db, shopping.ref_id)
    assert _index(trace, TraceEventType.APPROVAL_GRANTED) < _index(trace, TraceEventType.REVALIDATION_FAILED)
    assert _index(trace, TraceEventType.REVALIDATION_FAILED) < _index(trace, TraceEventType.REPLACEMENT_PROPOSED)
    assert _index(trace, TraceEventType.REPLACEMENT_PROPOSED) < _index(
        trace, TraceEventType.NEW_BASKET_VERSION_CREATED
    )
    failed = next(item for item in trace.revalidations if item.status.value != "PASS")
    assert "OUT_OF_STOCK" in failed.failure_reasons
    granted = next(item for item in trace.approvals if item.ref_id == request.ref_id)
    waiting = next(item for item in trace.approvals if item.ref_id == pending.ref_id)
    assert granted.covers == version_label(basket)
    assert granted.basket_version == 1
    assert waiting.covers == version_label(v2)
    assert waiting.basket_version == 2
    assert granted.covers != waiting.covers
    assert version_approval_covers(db, shopping, basket, action_ref_id="ACT-CHK") is True
    assert version_approval_covers(db, shopping, v2, action_ref_id="ACT-CHK") is False
    original = get_basket(db, basket.ref_id, version=1)
    assert [item.variant.ref_id for item in original.items] == HERO
    assert trace.outcome is TraceOutcome.IN_PROGRESS


def test_hero_payment_trace_keeps_client_and_verified_distinct(db: Session) -> None:
    shopping = _session(db)
    intent = _hard()
    _persist_intent(db, shopping, intent)
    basket = set_items(db, create_basket(db, shopping), HERO)
    request = create_approval_request(db, shopping, basket, action_ref_id="ACT-CHK")
    approve(db, request.ref_id)
    stub = StubPaymentProvider()
    payload = create_checkout_attempt(db, shopping, request.ref_id, intent=intent, provider=stub)
    reported = report_client_payment_result(
        db,
        payload.checkout_attempt_ref,
        razorpay_payment_id="pay_client",
        client_status="VERIFIED",
    )
    pending = build_agent_trace(db, shopping.ref_id)
    assert TraceEventType.CLIENT_PAYMENT_REPORTED in _types(pending)
    assert TraceEventType.PAYMENT_VERIFIED not in _types(pending)
    assert pending.payment_stages.client_status == "PAYMENT_REPORTED"
    assert pending.payment_stages.server_status != "VERIFIED"
    assert pending.outcome is TraceOutcome.PAYMENT_PENDING_VERIFICATION
    assert reported.payment_status.value != "VERIFIED"

    body = encode_razorpay_event(
        event="payment.captured",
        order_id=payload.provider_order_id,
        payment_id="pay_hero",
        amount_minor=HERO_PAISE,
    )
    process_webhook(db, stub, body=body, signature=stub.sign_webhook(body), event_id="evt_trace_pay")
    trace = build_agent_trace(db, shopping.ref_id)
    assert _index(trace, TraceEventType.CHECKOUT_CREATED) < _index(trace, TraceEventType.CLIENT_PAYMENT_REPORTED)
    assert _index(trace, TraceEventType.CLIENT_PAYMENT_REPORTED) < _index(
        trace, TraceEventType.PAYMENT_VERIFIED
    )
    assert TraceEventType.WEBHOOK_RECEIVED in _types(trace)
    assert TraceEventType.WEBHOOK_SIGNATURE_VERIFIED in _types(trace)
    client = next(item for item in trace.timeline_events if item.type is TraceEventType.CLIENT_PAYMENT_REPORTED)
    verified = next(item for item in trace.timeline_events if item.type is TraceEventType.PAYMENT_VERIFIED)
    assert client.details["client_reported"] is True
    assert verified.details["server_verified"] is True
    assert trace.payment_stages.client_status == "PAYMENT_REPORTED"
    assert trace.payment_stages.server_status == "VERIFIED"
    assert trace.payment_stages.payment_status == "VERIFIED"
    assert trace.outcome is TraceOutcome.PAYMENT_VERIFIED
    blob = json.dumps(trace.webhook_events[0].model_dump(mode="json"))
    assert "webhook_secret" not in blob
    assert "key_secret" not in blob
    assert "raw_body" not in blob
    assert trace.webhook_events[0].signature_valid is True
    assert trace.webhook_events[0].provider_event_id == "evt_trace_pay"


def test_no_upsell_trace_records_forgone_revenue_without_budget_violation(db: Session) -> None:
    shopping = _session(db)
    intent = _hard("2000")
    _persist_intent(db, shopping, intent)
    set_items(db, create_basket(db, shopping), ["SKU-001-M"])
    diagnosis = diagnose_friction(db, shopping, intent=intent)
    proposal = propose_growth_action(
        db, shopping, intent=intent, diagnosis=diagnosis, upsell_sku=DEMO_ATTACH_SKU
    )
    assert proposal.action is BoundedAction.NO_UPSELL
    validate_action(db, shopping, proposal, intent=intent)

    trace = build_agent_trace(db, shopping.ref_id)
    assert TraceEventType.NO_UPSELL in _types(trace)
    action = trace.agent_actions[0]
    assert action.action is BoundedAction.NO_UPSELL
    assert "HARD_BUDGET_VIOLATION" in action.reason_codes
    assert action.potential_revenue_not_pursued == Decimal("499.00")
    assert action.what_why_fix.fix == BoundedAction.NO_UPSELL.value
    assert trace.guardrails.hard_budget_violation_count == 0
    assert trace.checkout_attempts == []


def test_blocked_policy_trace_shows_proposal_without_execution(db: Session) -> None:
    shopping = _session(db)
    intent = _hard()
    _persist_intent(db, shopping, intent)
    basket = set_items(db, create_basket(db, shopping), HERO)
    before = [item.variant.ref_id for item in basket.items]
    evidence = record_evidence(
        db,
        session=shopping,
        kind=EvidenceKind.POLICY_DECISION.value,
        summary="lying GDE draft",
        payload={},
    )
    over = ["SKU-004-M", "SKU-007-M", "SKU-012-OS"]
    proposal = _draft(shopping, BoundedAction.REBUILD_BASKET, candidate_skus=over, evidence_ref_id=evidence.ref_id)
    row = AgentAction(
        ref_id=next_numeric_ref_id(db, AgentAction, RefPrefix.ACTION),
        session_id=shopping.id,
        action=proposal.action.value,
        reason=proposal.reason,
        reason_codes=["HARD_BUDGET_VIOLATION"],
        evidence_ref_ids=[evidence.ref_id],
        candidate_skus=over,
        confidence=proposal.confidence,
        what=proposal.what,
        why=proposal.why,
        fix=proposal.fix,
        status=ActionStatus.PROPOSED.value,
    )
    db.add(row)
    db.flush()
    proposal = proposal.model_copy(update={"ref_id": row.ref_id})
    result = validate_action(db, shopping, proposal, intent=intent)
    assert result.decision is PolicyVerdict.BLOCK

    trace = build_agent_trace(db, shopping.ref_id)
    assert any(item.ref_id == row.ref_id for item in trace.agent_actions)
    assert trace.policy_decisions[0].decision is PolicyVerdict.BLOCK
    budget = _named_check(trace, PolicyCheckName.HARD_BUDGET)
    assert budget.status is CheckStatus.FAIL
    assert budget.reason_code == "HARD_BUDGET_VIOLATION" or "HARD_BUDGET_VIOLATION" in trace.policy_decisions[0].reason_codes
    assert trace.checkout_attempts == []
    frozen = get_basket(db, basket.ref_id, version=1)
    assert [item.variant.ref_id for item in frozen.items] == before
    assert trace.guardrails.hard_budget_violation_count == 0
    assert trace.guardrails.invented_sku_count == 0


def test_final_outcome_stopped_from_stop_action(db: Session) -> None:
    shopping = _session(db)
    intent = _hard()
    evidence = record_evidence(
        db,
        session=shopping,
        kind=EvidenceKind.FRICTION_EVALUATION.value,
        summary="stop",
        payload={},
    )
    proposal = ProposedAction(
        session_ref_id=shopping.ref_id,
        friction_type=FrictionType.CATALOGUE_GAP,
        action=BoundedAction.STOP,
        reason="no viable path",
        evidence_ref_ids=[evidence.ref_id],
        confidence=Decimal("0.9"),
        what="CATALOGUE_GAP",
        why="no match",
        fix="STOP",
    )
    row = AgentAction(
        ref_id=next_numeric_ref_id(db, AgentAction, RefPrefix.ACTION),
        session_id=shopping.id,
        action=BoundedAction.STOP.value,
        reason=proposal.reason,
        reason_codes=["CATALOGUE_GAP"],
        evidence_ref_ids=[evidence.ref_id],
        confidence=proposal.confidence,
        what=proposal.what,
        why=proposal.why,
        fix=proposal.fix,
        status=ActionStatus.PROPOSED.value,
    )
    db.add(row)
    db.flush()
    validate_action(db, shopping, proposal.model_copy(update={"ref_id": row.ref_id}), intent=intent)
    trace = build_agent_trace(db, shopping.ref_id)
    assert TraceEventType.STOP in _types(trace)
    assert trace.outcome is TraceOutcome.STOPPED


def test_purchase_plan_rejected_outcome(db: Session) -> None:
    shopping = _session(db)
    basket = set_items(db, create_basket(db, shopping), HERO)
    request = create_approval_request(db, shopping, basket, action_ref_id="ACT-CHK")
    reject(db, request.ref_id)
    trace = build_agent_trace(db, shopping.ref_id)
    assert TraceEventType.APPROVAL_REJECTED in _types(trace)
    assert trace.outcome is TraceOutcome.PURCHASE_PLAN_REJECTED


def test_reconstruction_after_new_db_session(db: Session) -> None:
    shopping = _session(db)
    process_customer_message(
        db,
        session_ref_id=shopping.ref_id,
        message=HERO_UTTERANCE,
        provider=StubLLMProvider(),
    )
    set_items(db, create_basket(db, shopping), HERO)
    _fit_signals(shopping, db)
    intent = _hard()
    diagnosis = diagnose_friction(db, shopping, intent=intent)
    proposal = propose_growth_action(db, shopping, intent=intent, diagnosis=diagnosis)
    validate_action(db, shopping, proposal, intent=intent)
    db.commit()
    ref = shopping.ref_id
    original = build_agent_trace(db, ref)
    expected_types = _types(original)
    expected_outcome = original.outcome
    db.expunge_all()

    other = get_session_factory()()
    try:
        rebuilt = build_agent_trace(other, ref)
        assert rebuilt.session.ref_id == ref
        assert _types(rebuilt) == expected_types
        assert rebuilt.outcome is expected_outcome
        assert rebuilt.customer_intent is not None
        assert rebuilt.friction_diagnoses
        assert rebuilt.agent_actions
        assert rebuilt.policy_decisions
    finally:
        other.close()


def test_customer_progress_hides_merchant_sensitive_details(db: Session) -> None:
    shopping = _session(db)
    intent = _hard("2000")
    _persist_intent(db, shopping, intent)
    set_items(db, create_basket(db, shopping), ["SKU-001-M"])
    diagnosis = diagnose_friction(db, shopping, intent=intent)
    proposal = propose_growth_action(
        db, shopping, intent=intent, diagnosis=diagnosis, upsell_sku=DEMO_ATTACH_SKU
    )
    validate_action(db, shopping, proposal, intent=intent)

    merchant = build_agent_trace(db, shopping.ref_id)
    progress = project_customer_progress(merchant)
    assert merchant.current_basket is not None
    assert any(line.margin_percent is not None for line in merchant.current_basket.lines)
    assert progress.current_basket is not None
    assert all(line.margin_percent is None for line in progress.current_basket.lines)
    assert merchant.agent_actions[0].potential_revenue_not_pursued == Decimal("499.00")
    assert progress.agent_actions[0].potential_revenue_not_pursued is None
    blob = json.dumps(progress.model_dump(mode="json"))
    assert "guardrail" not in blob
    assert "499.00" not in blob
    dumped = json.dumps(progress.model_dump(mode="json"))
    assert "webhook_secret" not in dumped
    assert merchant.guardrails.hard_budget_violation_count == 0


def test_trace_and_progress_http(db: Session) -> None:
    shopping = _session(db)
    set_items(db, create_basket(db, shopping), HERO)

    def override_db():
        yield db

    app.dependency_overrides[get_db] = override_db
    try:
        with TestClient(app) as client:
            missing = client.get("/api/v1/sessions/SES-MISSING/trace")
            assert missing.status_code == 404
            full = client.get(f"/api/v1/sessions/{shopping.ref_id}/trace")
            assert full.status_code == 200
            body = full.json()
            assert body["session"]["ref_id"] == shopping.ref_id
            assert body["outcome"] == TraceOutcome.IN_PROGRESS.value
            assert "guardrails" in body
            progress = client.get(f"/api/v1/sessions/{shopping.ref_id}/progress")
            assert progress.status_code == 200
            assert "guardrails" not in progress.json()
    finally:
        app.dependency_overrides.clear()
