"""Checkout HTTP adapters. Business logic lives in layers.checkout."""

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.api.deps import get_db, payment_provider
from app.layers.basket import get_basket
from app.layers.checkout import (
    CheckoutServiceError,
    checkout_view,
    create_checkout_attempt,
    get_checkout_attempt,
    report_client_payment_result,
)
from app.layers.payments import PaymentProvider
from app.layers.session import require_session
from app.schemas.checkout import (
    CheckoutAttemptView,
    CheckoutPayload,
    ClientPaymentResultRequest,
    CreateCheckoutRequest,
)

router = APIRouter()


@router.post("/checkout", response_model=CheckoutPayload)
def create_checkout(
    body: CreateCheckoutRequest,
    db: Session = Depends(get_db),
    provider: PaymentProvider = Depends(payment_provider),
) -> CheckoutPayload:
    shopping = require_session(db, body.session_ref_id)
    basket = None
    if body.basket_ref_id:
        basket = get_basket(db, body.basket_ref_id)
        if basket is None:
            raise CheckoutServiceError("unknown_basket", f"Basket {body.basket_ref_id} was not found.")
    return create_checkout_attempt(
        db,
        shopping,
        body.approval_ref_id,
        basket=basket,
        provider=provider,
        claimed_amount=body.amount,
        claimed_amount_minor=body.amount_minor,
        claimed_price=body.price,
        claimed_discounted_total=body.discounted_total,
    )


@router.get("/checkout/{ref_id}", response_model=CheckoutAttemptView)
def get_checkout(
    ref_id: str,
    db: Session = Depends(get_db),
) -> CheckoutAttemptView:
    row = get_checkout_attempt(db, ref_id)
    if row is None:
        raise CheckoutServiceError("unknown_checkout", f"Checkout {ref_id} was not found.")
    return checkout_view(db, row)


@router.post("/checkout/{ref_id}/client-result", response_model=CheckoutAttemptView)
def client_payment_result(
    ref_id: str,
    body: ClientPaymentResultRequest,
    db: Session = Depends(get_db),
) -> CheckoutAttemptView:
    return report_client_payment_result(
        db,
        ref_id,
        provider_payment_id=body.provider_payment_id,
        razorpay_payment_id=body.razorpay_payment_id,
        razorpay_order_id=body.razorpay_order_id,
        razorpay_signature=body.razorpay_signature,
        client_status=body.client_status,
    )
