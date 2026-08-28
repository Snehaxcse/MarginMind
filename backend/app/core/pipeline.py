"""Message → intent → catalogue candidates. Not the Growth Decision Engine."""

from __future__ import annotations

from pydantic import ValidationError

from sqlalchemy.orm import Session

from app.layers.catalogue import filter_variants
from app.layers.evidence import record_customer_message
from app.layers.intent import intent_to_catalogue_inputs, persist_intent
from app.layers.session import append_session_event, require_session
from app.providers.llm.base import LLMProvider
from app.providers.llm.errors import ProviderError
from app.schemas.catalogue import CatalogueConstraints, SoftCatalogueSignals
from app.schemas.intent import IntentExtractionResult
from app.schemas.vocabulary import Actor, SessionEventType


class ProcessMessageResult:
    def __init__(
        self,
        *,
        ok: bool,
        session_ref_id: str,
        event_ref_id: str | None = None,
        evidence_ref_id: str | None = None,
        intent_ref_id: str | None = None,
        extraction: IntentExtractionResult | None = None,
        constraints: CatalogueConstraints | None = None,
        soft_signals: SoftCatalogueSignals | None = None,
        eligible_skus: list[str] | None = None,
        error_code: str | None = None,
        error_message: str | None = None,
    ) -> None:
        self.ok = ok
        self.session_ref_id = session_ref_id
        self.event_ref_id = event_ref_id
        self.evidence_ref_id = evidence_ref_id
        self.intent_ref_id = intent_ref_id
        self.extraction = extraction
        self.constraints = constraints
        self.soft_signals = soft_signals
        self.eligible_skus = eligible_skus or []
        self.error_code = error_code
        self.error_message = error_message


def process_customer_message(
    db: Session,
    *,
    session_ref_id: str,
    message: str,
    provider: LLMProvider,
) -> ProcessMessageResult:
    shopping = require_session(db, session_ref_id)
    evidence = record_customer_message(db, session=shopping, text=message)
    message_event = append_session_event(
        db,
        session=shopping,
        event_type=SessionEventType.CUSTOMER_MESSAGE.value,
        actor=Actor.CUSTOMER.value,
        payload={"text": message},
        evidence_ref_ids=[evidence.ref_id],
    )

    try:
        raw = provider.complete_structured(
            schema_name="intent_extraction",
            instructions="Return structured shopper intent only. Never invent SKUs or prices.",
            input_payload={"message": message, "evidence_ref_ids": [evidence.ref_id]},
        )
        if not isinstance(raw, dict):
            raise ProviderError("invalid_provider_payload", "Provider did not return an object.")
        raw["evidence_ref_ids"] = [evidence.ref_id]
        extraction = IntentExtractionResult.model_validate(raw)
    except (ProviderError, ValidationError) as exc:
        code = getattr(exc, "code", "intent_validation_failed")
        append_session_event(
            db,
            session=shopping,
            event_type=SessionEventType.PROVIDER_FAILED.value,
            actor=Actor.SYSTEM.value,
            payload={"error_code": code, "error": str(exc)},
            evidence_ref_ids=[evidence.ref_id],
        )
        return ProcessMessageResult(
            ok=False,
            session_ref_id=shopping.ref_id,
            event_ref_id=message_event.ref_id,
            evidence_ref_id=evidence.ref_id,
            error_code=code,
            error_message=str(exc),
        )

    stored = persist_intent(db, session=shopping, extraction=extraction)
    append_session_event(
        db,
        session=shopping,
        event_type=SessionEventType.INTENT_EXTRACTED.value,
        actor=Actor.SYSTEM.value,
        payload={"intent_ref_id": stored.ref_id, "confidence": extraction.confidence},
        evidence_ref_ids=[evidence.ref_id],
    )
    constraints, soft = intent_to_catalogue_inputs(
        extraction.intent, merchant_id=shopping.merchant_id
    )
    variants = filter_variants(db, constraints, soft=soft)
    return ProcessMessageResult(
        ok=True,
        session_ref_id=shopping.ref_id,
        event_ref_id=message_event.ref_id,
        evidence_ref_id=evidence.ref_id,
        intent_ref_id=stored.ref_id,
        extraction=extraction,
        constraints=constraints,
        soft_signals=soft,
        eligible_skus=[item.ref_id for item in variants],
    )
